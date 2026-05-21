"""Edge case tests for source commands."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from typer.testing import CliRunner

from antline.cli import app
from antline.core import db as db_module
from antline.core.models import SourceExploreReport, TableMeta

runner = CliRunner()
TODAY = date.today().strftime("%Y%m%d")


def test_source_explore_json_output(monkeypatch) -> None:
    """Explore with --json should output raw report data."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)

        # Init
        result = runner.invoke(app, ["init", "--path", ".", "--no-test-connection"])
        assert result.exit_code == 0

        # Add source without test
        result = runner.invoke(
            app,
            ["source", "add", "--type", "postgresql", "--host", "localhost",
             "--database", "testdb", "--user", "testuser", "--no-test-connection"],
        )
        assert result.exit_code == 0
        src_id = f"SRC-{TODAY}-001"

        # Mock get_engine to use SQLite (same pattern as test_e2e_workflow.py)
        sqlite_path = root / "fake_source.db"
        engine = create_engine(f"sqlite:///{sqlite_path}")
        monkeypatch.setattr(db_module, "get_engine", lambda _source, _password=None: engine)

        result = runner.invoke(app, ["source", "explore", src_id, "--json"], input="secret\n")
        assert result.exit_code == 0
        assert src_id in result.output or "Database" in result.output


def test_source_explore_not_found(monkeypatch) -> None:
    """Explore non-existent source should error."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)
        result = runner.invoke(app, ["init", "--path", ".", "--no-test-connection"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["source", "explore", "SRC-999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


def test_source_list_empty(monkeypatch) -> None:
    """List sources when none exist should show empty message."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.chdir(root)
        result = runner.invoke(app, ["init", "--path", ".", "--no-test-connection"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["source", "list"])
        assert result.exit_code == 0
        assert "No sources" in result.output
