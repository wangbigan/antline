"""Tests for DataRequirementAnalysisSkill."""

from __future__ import annotations

import json

import pytest

from antline.core.analysis_skill import (
    AnalysisResult,
    DataRequirementAnalysisSkill,
    _merge_gaps_into_sql,
    _safe_json_parse,
    _summarize_report,
)
from antline.core.models import (
    ColumnMeta,
    FieldStats,
    Requirement,
    SourceExploreReport,
    TableMeta,
    TargetField,
    TargetSchema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report() -> SourceExploreReport:
    return SourceExploreReport(
        source_id="SRC-20260520-001",
        tables=[
            TableMeta(
                name="patient_info",
                schema_name="public",
                row_count=100000,
                comment="患者基本信息",
                columns=[
                    ColumnMeta(
                        name="patient_id",
                        data_type="INTEGER",
                        nullable=False,
                        comment="患者ID",
                        stats=FieldStats(null_count=0, null_rate=0.0, unique_count=100000),
                        sample_data=[1, 2, 3],
                    ),
                    ColumnMeta(
                        name="name",
                        data_type="VARCHAR(50)",
                        nullable=False,
                        comment="姓名",
                        stats=FieldStats(null_count=0, null_rate=0.0),
                        sample_data=["张三", "李四"],
                    ),
                    ColumnMeta(
                        name="gender",
                        data_type="VARCHAR(2)",
                        nullable=True,
                        comment="性别",
                        stats=FieldStats(null_count=5000, null_rate=0.05),
                        sample_data=["男", "女"],
                    ),
                ],
                primary_key=["patient_id"],
            ),
            TableMeta(
                name="patient_address",
                schema_name="public",
                row_count=95000,
                comment="患者地址信息",
                columns=[
                    ColumnMeta(
                        name="patient_id",
                        data_type="INTEGER",
                        nullable=False,
                        stats=FieldStats(),
                    ),
                    ColumnMeta(
                        name="address",
                        data_type="VARCHAR(200)",
                        nullable=True,
                        comment="地址",
                        stats=FieldStats(null_count=10000, null_rate=0.105),
                        sample_data=["北京市", "上海市"],
                    ),
                ],
                primary_key=["patient_id"],
            ),
        ],
    )


def _make_requirement() -> Requirement:
    return Requirement(
        id="REQ-20260520-001",
        name="Test Requirement",
        target_schemas=[
            TargetSchema(
                table="patients",
                description="患者信息表",
                fields=[
                    TargetField(name="patient_id", data_type="INTEGER", nullable=False),
                    TargetField(name="patient_name", data_type="VARCHAR(50)", nullable=False),
                    TargetField(name="gender", data_type="VARCHAR(10)", nullable=True),
                    TargetField(name="home_address", data_type="VARCHAR(200)", nullable=True),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Source summarisation
# ---------------------------------------------------------------------------


class TestSummarizeReport:
    def test_includes_table_and_columns(self) -> None:
        report = _make_report()
        text = _summarize_report(report)
        assert "patient_info" in text
        assert "patient_id" in text
        assert "INTEGER" in text
        assert "样例" in text or "张三" in text

    def test_includes_null_rate(self) -> None:
        report = _make_report()
        text = _summarize_report(report)
        assert "null率5%" in text or "null_rate" in text


# ---------------------------------------------------------------------------
# Safe JSON parse
# ---------------------------------------------------------------------------


class TestSafeJsonParse:
    def test_plain_json(self) -> None:
        assert _safe_json_parse('{"a": 1}', {}) == {"a": 1}

    def test_markdown_fence(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        assert _safe_json_parse(raw, {}) == {"a": 1}

    def test_invalid_fallback(self) -> None:
        assert _safe_json_parse("not json", {"default": True}) == {"default": True}


# ---------------------------------------------------------------------------
# Merge gaps into SQL
# ---------------------------------------------------------------------------


class TestMergeGapsIntoSql:
    def test_appends_select_fields(self) -> None:
        base = """SELECT
    patient_id,
    name
FROM patients"""
        gaps = [
            {"target_field": "age", "source_field": "age", "transform": "CAST(age AS INTEGER)"}
        ]
        result = _merge_gaps_into_sql(base, gaps)
        assert "CAST(age AS INTEGER) AS age" in result
        assert "FROM patients" in result

    def test_adds_join_hint_for_new_table(self) -> None:
        base = """SELECT
    patient_id
FROM patients"""
        gaps = [
            {"target_field": "address", "source_table": "addresses", "source_field": "addr"}
        ]
        result = _merge_gaps_into_sql(base, gaps)
        assert "addr AS address" in result
        assert "TODO: JOIN addresses" in result

    def test_no_gaps_returns_unchanged(self) -> None:
        base = "SELECT * FROM patients"
        assert _merge_gaps_into_sql(base, []) == base


# ---------------------------------------------------------------------------
# Skill with mock LLM
# ---------------------------------------------------------------------------


class TestAnalysisSkillMock:
    """End-to-end skill tests using a deterministic mock LLM."""

    def test_full_pipeline_success(self) -> None:
        """Mock LLM returns perfect scope + SQL covering all fields."""

        def mock_llm(prompt: str) -> str:
            if "Table Scope Analysis" in prompt or "which source tables" in prompt.lower():
                return json.dumps({
                    "patients": {
                        "primary_source": {"table": "patient_info", "confidence": 0.95},
                        "join_sources": [
                            {"table": "patient_address", "join_key": "patient_id", "type": "left", "fields": ["address"]}
                        ],
                        "rationale": "test",
                        "confidence": 0.95,
                    }
                })
            if "dbt model SQL" in prompt.lower() or "Write a dbt model" in prompt:
                return json.dumps({
                    "map_sql": """SELECT
    p.patient_id AS patient_id,
    p.name AS patient_name,
    UPPER(COALESCE(p.gender, 'U')) AS gender,
    a.address AS home_address
FROM {{ source('SRC-001', 'patient_info') }} p
LEFT JOIN {{ source('SRC-001', 'patient_address') }} a ON p.patient_id = a.patient_id""",
                    "clean_rules": [
                        {"target_field": "patients.gender", "rules": ["uppercase", "coalesce_null"], "coalesce_default": "U"}
                    ],
                })
            if "Unmapped fields" in prompt or "not mapped" in prompt.lower():
                return json.dumps([])
            return json.dumps({})

        skill = DataRequirementAnalysisSkill(llm_call=mock_llm)
        req = _make_requirement()
        report = _make_report()
        result = skill.analyze(req, [report])

        assert result.confidence > 0.9
        assert "patients" in result.model_sqls
        assert result.approval_recommendation == "auto"
        assert not result.uncovered_fields
        assert len(result.clean_rules) >= 1
        assert any(cr.target_field == "patients.gender" for cr in result.clean_rules)

    def test_pipeline_with_gaps(self) -> None:
        """Mock LLM misses one field, gap-fill should catch it."""

        call_count = {"gap_fill": 0}

        def mock_llm(prompt: str) -> str:
            if "which source tables" in prompt.lower():
                return json.dumps({
                    "patients": {
                        "primary_source": {"table": "patient_info", "confidence": 0.9},
                        "join_sources": [],
                        "rationale": "test",
                        "confidence": 0.9,
                    }
                })
            if "Write a dbt model" in prompt:
                return json.dumps({
                    "map_sql": """SELECT
    patient_id AS patient_id,
    name AS patient_name,
    gender AS gender
FROM {{ source('SRC-001', 'patient_info') }}""",
                    "clean_rules": [],
                })
            if "not mapped" in prompt.lower() or "Unmapped fields" in prompt:
                call_count["gap_fill"] += 1
                return json.dumps([
                    {
                        "target_field": "home_address",
                        "source_table": "patient_address",
                        "source_field": "address",
                        "transform": "address",
                        "rationale": "Found in address table",
                    }
                ])
            return json.dumps({})

        skill = DataRequirementAnalysisSkill(llm_call=mock_llm)
        req = _make_requirement()
        report = _make_report()
        result = skill.analyze(req, [report])

        assert call_count["gap_fill"] >= 1
        assert "patients" in result.model_sqls
        # After gap-fill merge, home_address should be in SQL
        assert "home_address" in result.model_sqls["patients"]

    def test_no_llm_configured(self) -> None:
        """Default llm_call returns an error payload."""
        skill = DataRequirementAnalysisSkill()
        req = _make_requirement()
        report = _make_report()
        result = skill.analyze(req, [report])

        # Should gracefully handle bad JSON from default LLM
        assert result.confidence == 0.0
        assert result.approval_recommendation == "manual"

    def test_scope_only_step(self) -> None:
        def mock_llm(prompt: str) -> str:
            return json.dumps({
                "patients": {
                    "primary_source": {"table": "patient_info", "confidence": 0.92},
                    "join_sources": [],
                    "confidence": 0.92,
                }
            })

        skill = DataRequirementAnalysisSkill(llm_call=mock_llm)
        req = _make_requirement()
        source_text = _summarize_report(_make_report())
        scope = skill._step1_table_scope(req.target_schemas, source_text)

        assert "patients" in scope
        assert scope["patients"]["primary_source"]["table"] == "patient_info"
        assert scope["patients"]["confidence"] == 0.92

    def test_sql_generation_step(self) -> None:
        def mock_llm(prompt: str) -> str:
            return json.dumps({
                "map_sql": "SELECT patient_id AS patient_id FROM {{ source('SRC-001', 'patient_info') }}",
                "clean_rules": [],
            })

        skill = DataRequirementAnalysisSkill(llm_call=mock_llm)
        req = _make_requirement()
        ts = req.target_schemas[0]
        scope = {
            "primary_source": {"table": "patient_info"},
            "join_sources": [],
        }
        source_text = _summarize_report(_make_report())
        result = skill._step2_generate_sql(ts, scope, source_text)

        assert "map_sql" in result
        assert "patient_info" in result["map_sql"]


# ---------------------------------------------------------------------------
# Result model validation
# ---------------------------------------------------------------------------


class TestAnalysisResult:
    def test_defaults(self) -> None:
        r = AnalysisResult()
        assert r.model_sqls == {}
        assert r.uncovered_fields == []
        assert r.approval_recommendation == "manual"

    def test_model_dump(self) -> None:
        r = AnalysisResult(
            model_sqls={"patients": "SELECT 1"},
            confidence=0.95,
            approval_recommendation="auto",
        )
        data = r.model_dump(mode="json")
        assert data["confidence"] == 0.95
        assert data["approval_recommendation"] == "auto"
