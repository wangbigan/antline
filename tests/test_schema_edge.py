"""Edge case tests for schema commands."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antline.cli import app

runner = CliRunner()


def test_schema_import_file_not_found(monkeypatch) -> None:
    """Import non-existent CSV should error even inside a project."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)
        # Init project
        result = runner.invoke(app, ["init", "--path", ".", "--no-test-connection"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["schema", "import", "/nonexistent/file.csv"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "未找到" in result.output


def test_schema_import_outside_project(monkeypatch) -> None:
    """Import outside a project should still work (no git commit)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)

        csv_path = root / "schema.csv"
        csv_path.write_text(
            "module,table_name,table_comment,field_name,field_type,field_comment,example\n"
            "Hosp,patients,患者,id,INTEGER,患者ID,1\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["schema", "import", str(csv_path), "--output-dir", str(root / "out")])
        assert result.exit_code == 0
        assert "patients" in result.output
