"""Integration tests for `source explore` using SQLite in-memory database."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from antline.core import db as db_module
from antline.core.db import explore_source
from antline.core.models import DataSource, DataSourceType


@pytest.fixture
def sqlite_source():
    """Create a file-based SQLite source with sample tables."""
    db_path = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        conn.execute(
            text("""
                CREATE TABLE patients (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    age INTEGER,
                    gender TEXT,
                    admission_date TEXT
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO patients (id, name, age, gender, admission_date)
                VALUES
                    (1, 'Alice', 30, 'F', '2024-01-01'),
                    (2, 'Bob', 45, 'M', '2024-01-05'),
                    (3, 'Charlie', NULL, 'M', '2024-02-01')
            """)
        )
        conn.execute(
            text("""
                CREATE TABLE visits (
                    visit_id INTEGER PRIMARY KEY,
                    patient_id INTEGER NOT NULL,
                    visit_type TEXT,
                    cost REAL
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO visits (visit_id, patient_id, visit_type, cost)
                VALUES
                    (101, 1, 'outpatient', 150.5),
                    (102, 1, 'emergency', 500.0),
                    (103, 2, 'outpatient', 80.0)
            """)
        )
        conn.commit()

    yield DataSource(
        id="SRC-SQLITE",
        name="sqlite_test",
        db_type=DataSourceType.POSTGRESQL,
        host="",
        port=0,
        database=db_path,
        user="",
        password="",
    )

    Path(db_path).unlink(missing_ok=True)


def test_explore_source_basic(sqlite_source: DataSource, monkeypatch) -> None:
    """Test that explore_source returns metadata for all tables."""
    engine = create_engine(f"sqlite:///{sqlite_source.database}")
    monkeypatch.setattr(db_module, "get_engine", lambda _source: engine)

    report = explore_source(sqlite_source)

    assert report.source_id == "SRC-SQLITE"
    assert report.summary["total_tables"] == 2

    table_names = {t.name for t in report.tables}
    assert table_names == {"patients", "visits"}


def test_explore_source_table_metadata(sqlite_source: DataSource, monkeypatch) -> None:
    """Test table-level metadata extraction."""
    engine = create_engine(f"sqlite:///{sqlite_source.database}")
    monkeypatch.setattr(db_module, "get_engine", lambda _source: engine)

    report = explore_source(sqlite_source)

    patients = next(t for t in report.tables if t.name == "patients")
    assert patients.row_count == 3

    col_names = {c.name for c in patients.columns}
    assert col_names == {"id", "name", "age", "gender", "admission_date"}


def test_explore_source_column_stats(sqlite_source: DataSource, monkeypatch) -> None:
    """Test column statistics extraction."""
    engine = create_engine(f"sqlite:///{sqlite_source.database}")
    monkeypatch.setattr(db_module, "get_engine", lambda _source: engine)

    report = explore_source(sqlite_source)

    patients = next(t for t in report.tables if t.name == "patients")
    age_col = next(c for c in patients.columns if c.name == "age")

    assert age_col.stats.null_count == 1
    assert age_col.stats.null_rate == pytest.approx(1 / 3, rel=0.01)

    name_col = next(c for c in patients.columns if c.name == "name")
    assert name_col.stats.null_count == 0
    assert name_col.stats.is_unique_candidate is True


def test_explore_source_pk_detection(sqlite_source: DataSource, monkeypatch) -> None:
    """Test primary key detection."""
    engine = create_engine(f"sqlite:///{sqlite_source.database}")
    monkeypatch.setattr(db_module, "get_engine", lambda _source: engine)

    report = explore_source(sqlite_source)

    patients = next(t for t in report.tables if t.name == "patients")
    assert "id" in patients.primary_key

    visits = next(t for t in report.tables if t.name == "visits")
    assert "visit_id" in visits.primary_key
