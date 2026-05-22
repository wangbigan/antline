"""Tests for Coverage Audit (AST-based SQL field extraction)."""

from __future__ import annotations

from antline.core.analysis_skill import _audit_coverage, _extract_select_aliases


class TestExtractSelectAliases:
    """Test _extract_select_aliases with various SQL forms."""

    def test_simple_columns(self) -> None:
        sql = """SELECT
    patient_id,
    name,
    age
FROM patients"""
        aliases = _extract_select_aliases(sql)
        assert aliases == {"patient_id", "name", "age"}

    def test_aliased_columns(self) -> None:
        sql = """SELECT
    patient_id AS id,
    name AS patient_name,
    age AS patient_age
FROM patients"""
        aliases = _extract_select_aliases(sql)
        assert aliases == {"id", "patient_name", "patient_age"}

    def test_mixed_aliases(self) -> None:
        sql = """SELECT
    patient_id AS id,
    name,
    CAST(age AS INTEGER) AS age_int
FROM patients"""
        aliases = _extract_select_aliases(sql)
        assert aliases == {"id", "name", "age_int"}

    def test_with_join(self) -> None:
        sql = """SELECT
    p.patient_id AS id,
    p.name,
    a.address AS home_address
FROM patients p
LEFT JOIN addresses a ON p.patient_id = a.patient_id"""
        aliases = _extract_select_aliases(sql)
        assert aliases == {"id", "name", "home_address"}

    def test_coalesce_expression(self) -> None:
        sql = """SELECT
    COALESCE(patient_id, 0) AS patient_id,
    UPPER(name) AS name_upper
FROM patients"""
        aliases = _extract_select_aliases(sql)
        assert {"patient_id", "name_upper"}.issubset(aliases)

    def test_single_line_select(self) -> None:
        sql = "SELECT patient_id, name, age FROM patients"
        aliases = _extract_select_aliases(sql)
        assert aliases == {"patient_id", "name", "age"}

    def test_empty_sql(self) -> None:
        aliases = _extract_select_aliases("")
        assert aliases == set()


class TestAuditCoverage:
    """Test _audit_coverage diff against target schema fields."""

    def test_full_coverage(self) -> None:
        sql = """SELECT
    patient_id,
    name,
    age,
    gender
FROM patients"""
        target = ["patient_id", "name", "age", "gender"]
        uncovered = _audit_coverage(sql, target)
        assert uncovered == []

    def test_partial_coverage(self) -> None:
        sql = """SELECT
    patient_id,
    name
FROM patients"""
        target = ["patient_id", "name", "age", "gender"]
        uncovered = _audit_coverage(sql, target)
        assert uncovered == ["age", "gender"]

    def test_no_coverage(self) -> None:
        sql = "SELECT id FROM other_table"
        target = ["patient_id", "name"]
        uncovered = _audit_coverage(sql, target)
        assert uncovered == ["patient_id", "name"]

    def test_empty_sql(self) -> None:
        target = ["patient_id", "name"]
        uncovered = _audit_coverage("", target)
        assert uncovered == ["patient_id", "name"]

    def test_alias_coverage(self) -> None:
        sql = """SELECT
    patient_id AS id,
    full_name AS name
FROM patients"""
        target = ["id", "name", "age"]
        uncovered = _audit_coverage(sql, target)
        assert uncovered == ["age"]
