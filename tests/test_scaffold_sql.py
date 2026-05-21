"""Tests for scaffold using model_sqls (assessment-driven) and clean_rules."""

from __future__ import annotations

import subprocess
import tempfile
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from antline.cli import app

runner = CliRunner()
TODAY = date.today().strftime("%Y%m%d")


@pytest.fixture
def auto_assessed_project(monkeypatch) -> tuple[Path, str]:
    """Create a project where the requirement has auto-assessment with model_sqls."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)

        # Init workspace
        result = runner.invoke(app, ["init", "--path", ".", "--name", "test", "--no-test-connection"])
        assert result.exit_code == 0

        # Add source
        result = runner.invoke(
            app,
            ["source", "add", "--type", "postgresql", "--host", "localhost",
             "--database", "testdb", "--user", "testuser", "--no-test-connection"],
        )
        assert result.exit_code == 0
        src_id = f"SRC-{TODAY}-001"

        # Create explore report
        report = {
            "source_id": src_id,
            "tables": [
                {
                    "name": "patient_info",
                    "schema": "public",
                    "row_count": 100,
                    "columns": [
                        {"name": "patient_id", "data_type": "INTEGER", "nullable": False, "stats": {}, "sample_data": []},
                        {"name": "name", "data_type": "VARCHAR(50)", "nullable": False, "stats": {}, "sample_data": []},
                    ],
                    "primary_key": ["patient_id"],
                }
            ],
            "summary": {"database": "testdb", "db_type": "postgresql", "total_tables": 1, "total_rows": 100, "total_columns": 2},
        }
        explore_dir = root / "sources" / src_id / "explore"
        explore_dir.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(report, (explore_dir / "report.yml").open("w"), allow_unicode=True)

        # Create requirement + schema
        schema_path = root / "target_schema" / "patients.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(
            {"table": "patients", "description": "患者", "fields": [
                {"name": "patient_id", "data_type": "INTEGER", "nullable": False},
                {"name": "patient_name", "data_type": "VARCHAR(50)", "nullable": False},
            ]},
            schema_path.open("w", encoding="utf-8"),
            allow_unicode=True,
        )
        result = runner.invoke(
            app, ["requirement", "create", "--name", "Test", "--target-schema", str(schema_path)]
        )
        assert result.exit_code == 0
        req_id = f"REQ-{TODAY}-001"

        # Manually inject an auto-assessment with model_sqls
        req_dir = root / "requirements" / req_id
        assessment_yml = req_dir / "requirement.yml"
        req_data = yaml.safe_load(assessment_yml.read_text())
        req_data["assessment"] = {
            "feasible": True,
            "source_ids": [src_id],
            "field_mappings": [
                {"target_field": "patients.patient_id", "source_table": "patient_info", "source_field": "patient_id", "mapping_type": "direct", "confidence": 0.95},
                {"target_field": "patients.patient_name", "source_table": "patient_info", "source_field": "name", "mapping_type": "direct", "confidence": 0.9},
            ],
            "clean_rules": [
                {"target_field": "patients.patient_name", "rules": ["trim_whitespace", "coalesce_null"], "coalesce_default": "'未知'"},
            ],
            "model_sqls": {
                "patients": "SELECT\n    patient_id AS patient_id,\n    name AS patient_name\nFROM {{ source('SRC-20260520-001', 'patient_info') }}\n",
            },
            "engine_version": "2.0-llm",
            "auto_assessed": True,
        }
        yaml.safe_dump(req_data, assessment_yml.open("w", encoding="utf-8"), allow_unicode=True, sort_keys=False)

        # Approve
        result = runner.invoke(app, ["requirement", "approve", req_id, "--force"])
        assert result.exit_code == 0

        # Create project
        result = runner.invoke(app, ["project", "create", "--name", "Test Project", "--requirement", req_id])
        assert result.exit_code == 0
        prj_id = f"PRJ-{TODAY}-001"

        # Scaffold
        result = runner.invoke(
            app, ["project", "scaffold", prj_id, "--skip-db-setup", "--user", "u", "--password", "p"]
        )
        assert result.exit_code == 0

        yield root, prj_id


class TestScaffoldModelSqls:
    def test_map_model_uses_full_sql(self, auto_assessed_project) -> None:
        root, prj_id = auto_assessed_project
        map_sql_path = root / "projects" / prj_id / "dbt" / "models" / "map" / "map_patients.sql"
        assert map_sql_path.exists()
        content = map_sql_path.read_text()
        assert "patient_id AS patient_id" in content
        assert "name AS patient_name" in content
        assert "FROM {{ source(" in content

    def test_clean_model_applies_rules(self, auto_assessed_project) -> None:
        root, prj_id = auto_assessed_project
        clean_sql_path = root / "projects" / prj_id / "dbt" / "models" / "clean" / "clean_patients.sql"
        assert clean_sql_path.exists()
        content = clean_sql_path.read_text()
        assert "TRIM(" in content
        assert "COALESCE(" in content
        assert "patient_name" in content

    def test_no_todo_when_rules_present(self, auto_assessed_project) -> None:
        root, prj_id = auto_assessed_project
        clean_sql_path = root / "projects" / prj_id / "dbt" / "models" / "clean" / "clean_patients.sql"
        content = clean_sql_path.read_text()
        # Should NOT contain the big TODO block when clean_rules are applied
        assert "TODO: Add data cleaning logic" not in content

    def test_fallback_without_model_sqls(self, monkeypatch) -> None:
        """Without model_sqls, scaffold should fall back to field-by-field generation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monkeypatch.chdir(root)

            result = runner.invoke(app, ["init", "--path", ".", "--name", "test", "--no-test-connection"])
            assert result.exit_code == 0

            result = runner.invoke(
                app,
                ["source", "add", "--type", "postgresql", "--host", "localhost",
                 "--database", "testdb", "--user", "testuser", "--no-test-connection"],
            )
            assert result.exit_code == 0
            src_id = f"SRC-{TODAY}-001"

            schema_path = root / "target_schema" / "patients.yaml"
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            yaml.safe_dump(
                {"table": "patients", "fields": [
                    {"name": "patient_id", "data_type": "INTEGER", "nullable": False},
                ]},
                schema_path.open("w", encoding="utf-8"),
                allow_unicode=True,
            )
            result = runner.invoke(
                app, ["requirement", "create", "--name", "Test", "--target-schema", str(schema_path)]
            )
            assert result.exit_code == 0
            req_id = f"REQ-{TODAY}-001"

            # Manually inject assessment WITHOUT model_sqls (old style)
            req_dir = root / "requirements" / req_id
            assessment_yml = req_dir / "requirement.yml"
            req_data = yaml.safe_load(assessment_yml.read_text())
            req_data["assessment"] = {
                "feasible": True,
                "source_ids": [src_id],
                "field_mappings": [
                    {"target_field": "patients.patient_id", "source_table": "patient_info", "source_field": "patient_id", "mapping_type": "direct"},
                ],
            }
            yaml.safe_dump(req_data, assessment_yml.open("w", encoding="utf-8"), allow_unicode=True, sort_keys=False)

            result = runner.invoke(app, ["requirement", "approve", req_id, "--force"])
            assert result.exit_code == 0

            result = runner.invoke(app, ["project", "create", "--name", "Test", "--requirement", req_id])
            assert result.exit_code == 0
            prj_id = f"PRJ-{TODAY}-001"

            result = runner.invoke(
                app, ["project", "scaffold", prj_id, "--skip-db-setup", "--user", "u", "--password", "p"]
            )
            assert result.exit_code == 0

            map_sql_path = root / "projects" / prj_id / "dbt" / "models" / "map" / "map_patients.sql"
            content = map_sql_path.read_text()
            # Fallback generation should still produce valid SQL
            assert "patient_id" in content
            assert "FROM" in content

            clean_sql_path = root / "projects" / prj_id / "dbt" / "models" / "clean" / "clean_patients.sql"
            clean_content = clean_sql_path.read_text()
            # Without clean_rules, should have TODO block
            assert "TODO: Add data cleaning logic" in clean_content
