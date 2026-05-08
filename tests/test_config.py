"""Tests for Git-native state management."""

import tempfile
from pathlib import Path

import pytest

from antline.core.config import ProjectState
from antline.core.models import DataSource, DataSourceType, Requirement


@pytest.fixture
def temp_project():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create minimal antline.yml
        (root / "antline.yml").write_text("project:\n  name: test\n")
        yield root


from datetime import date


def test_next_source_id(temp_project):
    state = ProjectState(temp_project)
    today = date.today().strftime("%Y%m%d")
    assert state.next_source_id() == f"SRC-{today}-001"

    state.save_source(
        DataSource(
            id=f"SRC-{today}-001",
            name="s1",
            db_type=DataSourceType.POSTGRESQL,
            host="h",
            port=5432,
            database="db",
            user="u",
        )
    )
    assert state.next_source_id() == f"SRC-{today}-002"


def test_list_sources_empty(temp_project):
    state = ProjectState(temp_project)
    assert state.list_sources() == []


def test_save_and_get_source(temp_project):
    state = ProjectState(temp_project)
    src = DataSource(
        id="SRC-001",
        name="s1",
        db_type=DataSourceType.POSTGRESQL,
        host="localhost",
        port=5432,
        database="db",
        user="u",
    )
    state.save_source(src)
    loaded = state.get_source("SRC-001")
    assert loaded is not None
    assert loaded.name == "s1"
    assert loaded.host == "localhost"


def test_save_and_get_requirement(temp_project):
    state = ProjectState(temp_project)
    req = Requirement(id="REQ-001", name="r1")
    state.save_requirement(req)
    loaded = state.get_requirement("REQ-001")
    assert loaded is not None
    assert loaded.name == "r1"
