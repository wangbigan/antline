"""Extended tests for requirement commands: add-schema, approve edge cases."""

from __future__ import annotations

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
def workspace(monkeypatch) -> Path:
    """Create an initialized workspace with source, explored source, and requirement."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)

        # Init
        result = runner.invoke(app, ["init", "--path", ".", "--name", "test", "--no-test-connection"])
        assert result.exit_code == 0

        # Add source (no test connection)
        result = runner.invoke(
            app,
            ["source", "add", "--type", "postgresql", "--host", "localhost",
             "--database", "testdb", "--user", "testuser", "--no-test-connection"],
        )
        assert result.exit_code == 0
        src_id = f"SRC-{TODAY}-001"

        # Create explore report manually (for approve validation)
        report = {
            "source_id": src_id,
            "tables": [
                {
                    "name": "patients",
                    "schema": "public",
                    "row_count": 100,
                    "columns": [
                        {"name": "patient_id", "data_type": "INTEGER", "nullable": True, "stats": {}, "sample_data": []},
                        {"name": "patient_name", "data_type": "TEXT", "nullable": True, "stats": {}, "sample_data": []},
                    ],
                    "primary_key": ["patient_id"],
                }
            ],
            "summary": {"database": "testdb", "db_type": "postgresql", "total_tables": 1, "total_rows": 100, "total_columns": 2},
        }
        explore_dir = root / "sources" / src_id / "explore"
        explore_dir.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(report, (explore_dir / "report.yml").open("w"), allow_unicode=True)

        # Create requirement with target schema
        schema_path = root / "target_schema" / "patients.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(
            {"table": "patients", "description": "患者", "fields": [
                {"name": "patient_id", "data_type": "INTEGER", "nullable": False},
                {"name": "patient_name", "data_type": "TEXT", "nullable": True},
            ]},
            schema_path.open("w", encoding="utf-8"),
            allow_unicode=True,
        )
        result = runner.invoke(
            app, ["requirement", "create", "--name", "Test", "--target-schema", str(schema_path)]
        )
        assert result.exit_code == 0
        req_id = f"REQ-{TODAY}-001"

        # Assess (generates template)
        result = runner.invoke(app, ["requirement", "assess", req_id, src_id])
        assert result.exit_code == 0

        yield root, src_id, req_id


# ---------------------------------------------------------------------------
# add-schema
# ---------------------------------------------------------------------------


def test_requirement_add_schema_yaml(workspace) -> None:
    """add-schema with a YAML file should append schema to requirement."""
    root, _src_id, req_id = workspace

    new_schema = root / "new_schema.yaml"
    yaml.safe_dump(
        {"table": "admissions", "fields": [{"name": "id", "data_type": "INTEGER", "nullable": False}]},
        new_schema.open("w", encoding="utf-8"),
        allow_unicode=True,
    )

    result = runner.invoke(app, ["requirement", "add-schema", req_id, str(new_schema)])
    assert result.exit_code == 0
    assert "admissions" in result.output


def test_requirement_add_schema_directory(workspace) -> None:
    """add-schema with a directory should import all YAML files."""
    root, _src_id, req_id = workspace

    schema_dir = root / "schemas"
    schema_dir.mkdir()
    yaml.safe_dump(
        {"table": "procedures", "fields": [{"name": "id", "data_type": "INTEGER", "nullable": False}]},
        (schema_dir / "proc.yaml").open("w", encoding="utf-8"),
        allow_unicode=True,
    )

    result = runner.invoke(app, ["requirement", "add-schema", req_id, str(schema_dir)])
    assert result.exit_code == 0
    assert "procedures" in result.output


def test_requirement_add_schema_csv(workspace) -> None:
    """add-schema with a CSV file should import and convert to YAML."""
    root, _src_id, req_id = workspace

    csv_path = root / "schema.csv"
    csv_path.write_text(
        "module,table_name,table_comment,field_name,field_type,field_comment,example\n"
        "Hosp,diagnoses,诊断表,diag_id,INTEGER,诊断ID,1\n"
        "Hosp,diagnoses,诊断表,diag_name,VARCHAR(100),诊断名称,感冒\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["requirement", "add-schema", req_id, str(csv_path)])
    assert result.exit_code == 0
    assert "diagnoses" in result.output


def test_requirement_add_schema_duplicate(workspace) -> None:
    """add-schema with an existing table should skip it."""
    root, _src_id, req_id = workspace

    # patients table already exists from fixture
    schema_path = root / "dup_schema.yaml"
    yaml.safe_dump(
        {"table": "patients", "fields": [{"name": "id", "data_type": "INTEGER", "nullable": False}]},
        schema_path.open("w", encoding="utf-8"),
        allow_unicode=True,
    )

    result = runner.invoke(app, ["requirement", "add-schema", req_id, str(schema_path)])
    assert result.exit_code == 0
    assert "Skipped duplicate" in result.output


def test_requirement_add_schema_not_found(workspace) -> None:
    """add-schema with non-existent path should error."""
    _root, _src_id, req_id = workspace
    result = runner.invoke(app, ["requirement", "add-schema", req_id, "/nonexistent/file.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_add_schema_req_not_found(workspace) -> None:
    """add-schema for non-existent requirement should error."""
    root, _src_id, _req_id = workspace
    schema_path = root / "x.yaml"
    yaml.safe_dump({"table": "x", "fields": []}, schema_path.open("w", encoding="utf-8"), allow_unicode=True)
    result = runner.invoke(app, ["requirement", "add-schema", "REQ-999", str(schema_path)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# approve edge cases
# ---------------------------------------------------------------------------


def _write_assessment(root: Path, req_id: str, data: dict) -> Path:
    """Write a valid assessment.md with YAML frontmatter."""
    assessment_dir = root / "requirements" / req_id / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    md = f"---\n{frontmatter.rstrip()}\n---\n\n# Assessment\n"
    path = assessment_dir / "assessment.md"
    path.write_text(md, encoding="utf-8")
    return path


def test_requirement_approve_already_approved_no_force(workspace) -> None:
    """Approve an already-approved requirement without --force should warn."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": True,
        "source_ids": [src_id],
        "field_mappings": [
            {"target_field": "patients.patient_id", "source_table": "patients", "source_field": "patient_id", "mapping_type": "direct", "risk": "low"},
        ],
    })

    # First approve
    result = runner.invoke(app, ["requirement", "approve", req_id])
    assert result.exit_code == 0

    # Second approve without --force
    result = runner.invoke(app, ["requirement", "approve", req_id])
    assert result.exit_code == 1
    assert "已经审批通过" in result.output or "already approved" in result.output.lower()


def test_requirement_approve_force_reapprove(workspace) -> None:
    """Re-approve with --force should work."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": True,
        "source_ids": [src_id],
        "field_mappings": [
            {"target_field": "patients.patient_id", "source_table": "patients", "source_field": "patient_id", "mapping_type": "direct", "risk": "low"},
        ],
    })

    # First approve
    result = runner.invoke(app, ["requirement", "approve", req_id])
    assert result.exit_code == 0

    # Re-approve with --force
    result = runner.invoke(app, ["requirement", "approve", req_id, "--force", "--note", "修正映射"])
    assert result.exit_code == 0


def test_requirement_approve_no_frontmatter(workspace) -> None:
    """Approve file without YAML frontmatter should error."""
    root, _src_id, req_id = workspace

    assessment_dir = root / "requirements" / req_id / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    path = assessment_dir / "assessment.md"
    path.write_text("# No frontmatter here\n", encoding="utf-8")

    result = runner.invoke(app, ["requirement", "approve", req_id, "--file", str(path)])
    assert result.exit_code == 1
    assert "frontmatter" in result.output.lower() or "格式错误" in result.output


def test_requirement_approve_empty_mappings(workspace) -> None:
    """Approve with empty field_mappings should error."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": True,
        "source_ids": [src_id],
        "field_mappings": [],
    })

    result = runner.invoke(app, ["requirement", "approve", req_id])
    assert result.exit_code == 1
    assert "缺少字段映射" in result.output or "field_mappings" in result.output.lower()


def test_requirement_approve_not_feasible(workspace) -> None:
    """Approve not-feasible assessment without --force should error."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": False,
        "source_ids": [src_id],
        "field_mappings": [
            {"target_field": "patients.patient_id", "source_table": "patients", "source_field": "patient_id", "mapping_type": "direct", "risk": "low"},
        ],
    })

    result = runner.invoke(app, ["requirement", "approve", req_id])
    assert result.exit_code == 1
    assert "不可行" in result.output or "not feasible" in result.output.lower()


def test_requirement_approve_not_feasible_force(workspace) -> None:
    """Approve not-feasible with --force should succeed."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": False,
        "source_ids": [src_id],
        "field_mappings": [
            {"target_field": "patients.patient_id", "source_table": "patients", "source_field": "patient_id", "mapping_type": "direct", "risk": "low"},
        ],
    })

    result = runner.invoke(app, ["requirement", "approve", req_id, "--force"])
    assert result.exit_code == 0


def test_requirement_approve_validation_error_force(workspace) -> None:
    """Approve with validation errors and --force should succeed."""
    root, src_id, req_id = workspace

    _write_assessment(root, req_id, {
        "feasible": True,
        "source_ids": [src_id],
        "field_mappings": [
            # nonexistent table/field should trigger validation warning
            {"target_field": "patients.x", "source_table": "nonexistent_table", "source_field": "x", "mapping_type": "direct", "risk": "low"},
        ],
    })

    result = runner.invoke(app, ["requirement", "approve", req_id, "--force"])
    assert result.exit_code == 0
    assert "validation" in result.output.lower() or "force" in result.output.lower()


def test_requirement_approve_file_not_found(workspace) -> None:
    """Approve with missing assessment file should error."""
    _root, _src_id, req_id = workspace
    result = runner.invoke(app, ["requirement", "approve", req_id, "--file", "/nonexistent/assessment.md"])
    assert result.exit_code == 1
    assert "未找到" in result.output or "not found" in result.output.lower()
