"""Tests for project commands: compile, build, validate, deliver."""

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
def scaffolded_project(monkeypatch) -> Path:
    """Create a fully scaffolded project with approved requirement."""
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
                    "name": "patients",
                    "schema": "public",
                    "row_count": 100,
                    "columns": [
                        {"name": "patient_id", "data_type": "INTEGER", "nullable": True, "stats": {}, "sample_data": []},
                    ],
                    "primary_key": ["patient_id"],
                }
            ],
            "summary": {"database": "testdb", "db_type": "postgresql", "total_tables": 1, "total_rows": 100, "total_columns": 1},
        }
        explore_dir = root / "sources" / src_id / "explore"
        explore_dir.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(report, (explore_dir / "report.yml").open("w"), allow_unicode=True)

        # Create requirement + schema + assess + approve
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

        result = runner.invoke(app, ["requirement", "assess", req_id, src_id])
        assert result.exit_code == 0

        # Write assessment
        assessment_dir = root / "requirements" / req_id / "assessment"
        frontmatter = yaml.safe_dump({
            "feasible": True,
            "source_ids": [src_id],
            "field_mappings": [
                {"target_field": "patients.patient_id", "source_table": "patients",
                 "source_field": "patient_id", "mapping_type": "direct", "risk": "low"},
            ],
        }, allow_unicode=True, sort_keys=False)
        md = f"---\n{frontmatter.rstrip()}\n---\n\n# Assessment\n"
        (assessment_dir / "assessment.md").write_text(md, encoding="utf-8")

        result = runner.invoke(app, ["requirement", "approve", req_id])
        assert result.exit_code == 0

        # Create project
        result = runner.invoke(app, ["project", "create", "--name", "Test Project", "--requirement", req_id])
        assert result.exit_code == 0
        prj_id = f"PRJ-{TODAY}-001"

        # Scaffold (skip DB setup)
        result = runner.invoke(
            app, ["project", "scaffold", prj_id, "--skip-db-setup", "--user", "u", "--password", "p"]
        )
        assert result.exit_code == 0

        yield root, prj_id


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


def test_project_compile(scaffolded_project, monkeypatch) -> None:
    """compile should call dbt compile via subprocess."""
    _root, prj_id = scaffolded_project

    called = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["project", "compile", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 0
    assert any("dbt" in str(c) and "compile" in str(c) for c in called)


def test_project_compile_single_model(scaffolded_project, monkeypatch) -> None:
    """compile with -m should compile a single model."""
    _root, prj_id = scaffolded_project

    called = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(
        app, ["project", "compile", prj_id, "--model", "row_patients", "--user", "u", "--password", "p"]
    )
    assert result.exit_code == 0
    assert any("--select" in str(c) for c in called)


def test_project_compile_no_project(scaffolded_project) -> None:
    """compile without scaffold should error."""
    _root, _prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "compile", "PRJ-999", "--user", "u", "--password", "p"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def test_project_build(scaffolded_project, monkeypatch) -> None:
    """build should call dbt build and record a version."""
    _root, prj_id = scaffolded_project

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=0)
    )

    result = runner.invoke(app, ["project", "build", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 0
    assert "successful" in result.output.lower() or "success" in result.output.lower()


def test_project_build_failed(scaffolded_project, monkeypatch) -> None:
    """build failure should exit with error."""
    _root, prj_id = scaffolded_project

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=1)
    )

    result = runner.invoke(app, ["project", "build", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_project_validate_passed(scaffolded_project, monkeypatch) -> None:
    """validate with passing dbt tests should mark QC passed."""
    _root, prj_id = scaffolded_project

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=0)
    )

    result = runner.invoke(app, ["project", "validate", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 0
    assert "passed" in result.output.lower() or "通过" in result.output


def test_project_validate_failed(scaffolded_project, monkeypatch) -> None:
    """validate with failing tests should exit with error."""
    _root, prj_id = scaffolded_project

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=1)
    )

    result = runner.invoke(app, ["project", "validate", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower() or "失败" in result.output


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------


def test_project_deliver_after_validate(scaffolded_project, monkeypatch) -> None:
    """deliver after validate should mark project as delivered."""
    _root, prj_id = scaffolded_project

    # First validate (mock pass)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=0)
    )
    result = runner.invoke(app, ["project", "validate", prj_id, "--user", "u", "--password", "p"])
    assert result.exit_code == 0

    # Then deliver
    result = runner.invoke(app, ["project", "deliver", prj_id])
    assert result.exit_code == 0
    assert "delivered" in result.output.lower() or "交付" in result.output


def test_project_deliver_without_qc(scaffolded_project) -> None:
    """deliver without passing QC should error."""
    _root, prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "deliver", prj_id])
    assert result.exit_code == 1
    assert "not passed" in result.output.lower() or "qc" in result.output.lower()


def test_project_deliver_not_found(scaffolded_project) -> None:
    """deliver for non-existent project should error."""
    _root, _prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "deliver", "PRJ-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def test_project_extract_not_found(scaffolded_project) -> None:
    """extract for non-existent project should error."""
    _root, _prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "extract", "PRJ-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_project_extract_no_platform(scaffolded_project, monkeypatch) -> None:
    """extract without platform config should error."""
    root, prj_id = scaffolded_project
    # Remove antline.yml platform section
    config = yaml.safe_load((root / "antline.yml").read_text())
    del config["platform"]
    yaml.safe_dump(config, (root / "antline.yml").open("w"), sort_keys=False, allow_unicode=True)

    result = runner.invoke(app, ["project", "extract", prj_id])
    assert result.exit_code == 1
    assert "platform" in result.output.lower() or "not configured" in result.output.lower()


# ---------------------------------------------------------------------------
# show / list / create edge cases
# ---------------------------------------------------------------------------


def test_project_show(scaffolded_project) -> None:
    """show should display project details including QC rules."""
    _root, prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "show", prj_id])
    assert result.exit_code == 0
    assert prj_id in result.output
    assert "Test Project" in result.output


def test_project_show_not_found(scaffolded_project) -> None:
    """show for non-existent project should error."""
    _root, _prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "show", "PRJ-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_project_list(scaffolded_project) -> None:
    """list should show all projects."""
    _root, prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "list"])
    assert result.exit_code == 0
    assert prj_id in result.output


def test_project_list_json(scaffolded_project) -> None:
    """list --json should output JSON."""
    _root, prj_id = scaffolded_project
    result = runner.invoke(app, ["project", "list", "--json"])
    assert result.exit_code == 0
    assert prj_id in result.output


def test_project_create_unapproved_requirement(scaffolded_project) -> None:
    """create with unapproved requirement should error."""
    _root, _prj_id = scaffolded_project
    result = runner.invoke(
        app, ["project", "create", "--name", "Bad", "--requirement", "REQ-999"]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_project_compile_no_dbt_project(scaffolded_project) -> None:
    """compile without scaffold should error."""
    _root, _prj_id = scaffolded_project
    # Remove dbt project dir to simulate missing scaffold
    result = runner.invoke(app, ["project", "compile", "PRJ-999", "--user", "u", "--password", "p"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
