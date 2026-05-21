"""Tests for schema, source, and requirement commands (list/show/update/remove)."""

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
    """Create an initialized Antline workspace with sample data."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)

        # Init workspace
        result = runner.invoke(app, ["init", "--path", ".", "--name", "test-ws", "--no-test-connection"])
        assert result.exit_code == 0

        # Add a source
        result = runner.invoke(
            app,
            ["source", "add", "--type", "postgresql", "--host", "localhost",
             "--database", "testdb", "--user", "testuser", "--no-test-connection"],
        )
        assert result.exit_code == 0

        # Create a requirement
        schema_path = root / "target_schema" / "patients.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        yaml.safe_dump(
            {"table": "patients", "description": "患者", "fields": [
                {"name": "id", "data_type": "INTEGER", "nullable": False},
            ]},
            schema_path.open("w", encoding="utf-8"),
            allow_unicode=True,
        )
        result = runner.invoke(
            app, ["requirement", "create", "--name", "Test Req", "--target-schema", str(schema_path)]
        )
        assert result.exit_code == 0

        yield root


# ---------------------------------------------------------------------------
# Schema commands
# ---------------------------------------------------------------------------


def test_schema_list(workspace: Path) -> None:
    """Schema list should show all imported schemas."""
    result = runner.invoke(app, ["schema", "list"])
    assert result.exit_code == 0
    assert "patients" in result.output


def test_schema_list_empty(workspace: Path) -> None:
    """Schema list with no schemas should show empty message."""
    result = runner.invoke(app, ["schema", "list", "--dir", "nonexistent"])
    assert result.exit_code == 0
    assert "No schema directory" in result.output or "No schema files" in result.output


def test_schema_show(workspace: Path) -> None:
    """Schema show should display a single schema definition."""
    result = runner.invoke(app, ["schema", "show", "patients"])
    assert result.exit_code == 0
    assert "patients" in result.output
    assert "id" in result.output


def test_schema_show_not_found(workspace: Path) -> None:
    """Schema show for non-existent table should error."""
    result = runner.invoke(app, ["schema", "show", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Source commands
# ---------------------------------------------------------------------------


def test_source_list(workspace: Path) -> None:
    """Source list should show all configured sources."""
    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0
    src_id = f"SRC-{TODAY}-001"
    assert src_id in result.output


def test_source_show(workspace: Path) -> None:
    """Source show should display source details."""
    src_id = f"SRC-{TODAY}-001"
    result = runner.invoke(app, ["source", "show", src_id])
    assert result.exit_code == 0
    assert src_id in result.output
    assert "testdb" in result.output


def test_source_show_not_found(workspace: Path) -> None:
    """Source show for non-existent ID should error."""
    result = runner.invoke(app, ["source", "show", "SRC-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_source_update(workspace: Path) -> None:
    """Source update should modify specified fields."""
    src_id = f"SRC-{TODAY}-001"
    result = runner.invoke(
        app, ["source", "update", src_id, "--name", "Updated Name", "--host", "newhost"]
    )
    assert result.exit_code == 0
    assert "Updated" in result.output

    # Verify persistence
    result = runner.invoke(app, ["source", "show", src_id])
    assert "Updated Name" in result.output
    assert "newhost" in result.output


def test_source_update_not_found(workspace: Path) -> None:
    """Source update for non-existent ID should error."""
    result = runner.invoke(app, ["source", "update", "SRC-999", "--name", "X"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_source_remove_force(workspace: Path) -> None:
    """Source remove with --force should delete without prompt."""
    src_id = f"SRC-{TODAY}-001"
    result = runner.invoke(app, ["source", "remove", src_id, "--force"])
    assert result.exit_code == 0
    assert "Removed" in result.output

    # Verify deletion
    result = runner.invoke(app, ["source", "show", src_id])
    assert result.exit_code == 1


def test_source_remove_not_found(workspace: Path) -> None:
    """Source remove for non-existent ID should error."""
    result = runner.invoke(app, ["source", "remove", "SRC-999", "--force"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_source_list_json(workspace: Path) -> None:
    """Source list --json should output JSON."""
    result = runner.invoke(app, ["source", "list", "--json"])
    assert result.exit_code == 0
    src_id = f"SRC-{TODAY}-001"
    assert src_id in result.output


# ---------------------------------------------------------------------------
# Requirement commands
# ---------------------------------------------------------------------------


def test_requirement_list(workspace: Path) -> None:
    """Requirement list should show all requirements."""
    result = runner.invoke(app, ["requirement", "list"])
    assert result.exit_code == 0
    req_id = f"REQ-{TODAY}-001"
    assert req_id in result.output


def test_requirement_show(workspace: Path) -> None:
    """Requirement show should display requirement details."""
    req_id = f"REQ-{TODAY}-001"
    result = runner.invoke(app, ["requirement", "show", req_id])
    assert result.exit_code == 0
    assert req_id in result.output
    assert "Test Req" in result.output


def test_requirement_show_not_found(workspace: Path) -> None:
    """Requirement show for non-existent ID should error."""
    result = runner.invoke(app, ["requirement", "show", "REQ-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_update(workspace: Path) -> None:
    """Requirement update should modify fields."""
    req_id = f"REQ-{TODAY}-001"
    result = runner.invoke(
        app, ["requirement", "update", req_id, "--name", "Updated Req", "--background", "bg"]
    )
    assert result.exit_code == 0
    assert "Updated" in result.output

    result = runner.invoke(app, ["requirement", "show", req_id])
    assert "Updated Req" in result.output
    assert "bg" in result.output


def test_requirement_update_not_found(workspace: Path) -> None:
    """Requirement update for non-existent ID should error."""
    result = runner.invoke(app, ["requirement", "update", "REQ-999", "--name", "X"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_remove_force(workspace: Path) -> None:
    """Requirement remove with --force should delete without prompt."""
    req_id = f"REQ-{TODAY}-001"
    result = runner.invoke(app, ["requirement", "remove", req_id, "--force"])
    assert result.exit_code == 0
    assert "Removed" in result.output

    result = runner.invoke(app, ["requirement", "show", req_id])
    assert result.exit_code == 1


def test_requirement_remove_not_found(workspace: Path) -> None:
    """Requirement remove for non-existent ID should error."""
    result = runner.invoke(app, ["requirement", "remove", "REQ-999", "--force"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_list_json(workspace: Path) -> None:
    """Requirement list --json should output JSON."""
    result = runner.invoke(app, ["requirement", "list", "--json"])
    assert result.exit_code == 0
    req_id = f"REQ-{TODAY}-001"
    assert req_id in result.output


# ---------------------------------------------------------------------------
# CLI status
# ---------------------------------------------------------------------------


def test_cli_status(workspace: Path) -> None:
    """Status command should show workspace overview."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "test-ws" in result.output
    assert "Source" in result.output
    assert "Requirement" in result.output


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_requirement_create_schema_not_found(workspace: Path) -> None:
    """Create requirement with non-existent schema path should error."""
    result = runner.invoke(
        app, ["requirement", "create", "--name", "Bad", "--target-schema", "/nonexistent/schema.yaml"]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "未找到" in result.output


def test_requirement_assess_not_found(workspace: Path) -> None:
    """Assess non-existent requirement should error."""
    result = runner.invoke(app, ["requirement", "assess", "REQ-999", "SRC-001"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_assess_source_not_found(workspace: Path) -> None:
    """Assess with non-existent source should error."""
    req_id = f"REQ-{TODAY}-001"
    result = runner.invoke(app, ["requirement", "assess", req_id, "SRC-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_requirement_assess_no_schema(workspace: Path) -> None:
    """Assess requirement without target schema should error."""
    req_id = f"REQ-{TODAY}-001"
    # Create a requirement with no schema by making a new one
    result = runner.invoke(app, ["requirement", "create", "--name", "NoSchema"])
    assert result.exit_code == 0
    req_id2 = f"REQ-{TODAY}-002"
    result = runner.invoke(app, ["requirement", "assess", req_id2, f"SRC-{TODAY}-001"])
    assert result.exit_code == 1
    assert "no target schema" in result.output.lower() or "没有目标" in result.output


def test_source_add_connection_error(workspace: Path, monkeypatch) -> None:
    """Add source with connection failure should error gracefully."""
    from antline.core import db as db_module

    def fake_get_engine(source, password):
        raise Exception("connection refused")

    monkeypatch.setattr(db_module, "get_engine", fake_get_engine)

    result = runner.invoke(
        app,
        ["source", "add", "--type", "postgresql", "--host", "badhost",
         "--database", "db", "--user", "u", "--password", "p"],
    )
    assert result.exit_code == 1
    assert "failed" in result.output.lower() or "connect" in result.output.lower()


def test_cli_init_connection_fail(monkeypatch) -> None:
    """Init with failing connection should error."""
    import sqlalchemy

    def fake_create_engine(conn_str, **kwargs):
        raise Exception("connection refused")

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    result = runner.invoke(
        app, ["init", "--path", ".", "--name", "fail", "--db-type", "postgresql",
              "--host", "badhost", "--user", "u", "--password", "p"]
    )
    assert result.exit_code == 1
