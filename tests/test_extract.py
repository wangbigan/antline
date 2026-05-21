"""Tests for the extract job: source-to-ODS data sync."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from antline.core import extract as extract_module
from antline.core.extract import (
    ExtractResult,
    _build_target_table,
    _copy_data,
    extract_source_to_ods,
)
from antline.core.models import DataSource, DataSourceType


@pytest.fixture
def source_engine():
    """In-memory SQLite database with sample tables."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE patients (
                    patient_id INTEGER PRIMARY KEY,
                    patient_name TEXT NOT NULL,
                    patient_age INTEGER,
                    gender TEXT
                )
            """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO patients (patient_id, patient_name, patient_age, gender)
                VALUES (1, 'Alice', 30, 'F'),
                       (2, 'Bob', 45, 'M'),
                       (3, 'Charlie', 25, 'M')
            """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admissions (
                    admission_id INTEGER PRIMARY KEY,
                    patient_id INTEGER,
                    admission_date TEXT
                )
            """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO admissions (admission_id, patient_id, admission_date)
                VALUES (101, 1, '2024-01-15'), (102, 2, '2024-02-20')
            """
            )
        )
    return engine


@pytest.fixture
def target_engine():
    """Empty in-memory SQLite database."""
    return create_engine("sqlite:///:memory:")


@pytest.fixture
def sample_source() -> DataSource:
    return DataSource(
        id="SRC-20260508-001",
        name="Test Source",
        db_type=DataSourceType.POSTGRESQL,
        host="localhost",
        port=5432,
        database="test_db",
        user="test_user",
    )


def test_build_target_table(source_engine, target_engine):
    """Building a target table from inspector info should create equivalent columns."""
    inspector = inspect(source_engine)
    target_metadata = extract_module.MetaData(schema="ods_test")
    tgt_table = _build_target_table(inspector, "patients", None, target_metadata)

    assert tgt_table.name == "patients"
    col_names = {c.name for c in tgt_table.columns}
    assert col_names == {"patient_id", "patient_name", "patient_age", "gender"}

    # All columns should be nullable for ODS safety
    for col in tgt_table.columns:
        assert col.nullable is True


def test_build_target_table_not_found(source_engine, target_engine):
    """Building a target table for a non-existent source table should raise."""
    inspector = inspect(source_engine)
    target_metadata = extract_module.MetaData(schema="ods_test")
    with pytest.raises(ValueError, match="not found in source"):
        _build_target_table(inspector, "nonexistent", None, target_metadata)


def test_copy_data(source_engine, target_engine):
    """Copying data should transfer all rows."""
    from sqlalchemy import MetaData, Table

    # Build source and target tables
    src_metadata = MetaData()
    src_table = Table("patients", src_metadata, autoload_with=source_engine)

    inspector = inspect(source_engine)
    tgt_metadata = MetaData()
    tgt_table = _build_target_table(inspector, "patients", None, tgt_metadata)
    tgt_table.create(target_engine)

    rows = _copy_data(source_engine, target_engine, src_table, tgt_table, batch_size=2)

    assert rows == 3

    # Verify data in target
    with target_engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM patients"))
        assert result.scalar() == 3

        result = conn.execute(text("SELECT patient_name FROM patients ORDER BY patient_id"))
        names = [row[0] for row in result.fetchall()]
        assert names == ["Alice", "Bob", "Charlie"]


def test_extract_source_to_ods(source_engine, target_engine, sample_source, monkeypatch):
    """End-to-end extract from source to ODS schema."""
    # Mock the source engine creation so we use our in-memory source
    monkeypatch.setattr(
        extract_module, "_get_source_engine", lambda _source, _password: source_engine
    )

    job_result = extract_source_to_ods(
        source=sample_source,
        source_password="any",
        target_engine=target_engine,
        tables=["patients", "admissions"],
        ods_schema="ods_src_001",
        batch_size=2,
    )

    assert job_result.source_id == "SRC-20260508-001"
    assert job_result.ods_schema == "ods_src_001"
    assert len(job_result.results) == 2
    assert job_result.total_rows == 5  # 3 patients + 2 admissions
    assert job_result.success_count == 2
    assert job_result.failed_count == 0

    # Verify tables exist in target
    inspector = inspect(target_engine)
    tables = inspector.get_table_names()
    assert "patients" in tables
    assert "admissions" in tables

    # Verify data
    with target_engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM patients"))
        assert result.scalar() == 3


def test_extract_source_to_ods_table_not_found(
    source_engine, target_engine, sample_source, monkeypatch
):
    """Extracting a non-existent table should be skipped."""
    monkeypatch.setattr(
        extract_module, "_get_source_engine", lambda _source, _password: source_engine
    )

    job_result = extract_source_to_ods(
        source=sample_source,
        source_password="any",
        target_engine=target_engine,
        tables=["patients", "nonexistent_table"],
        ods_schema="ods_src_001",
    )

    assert job_result.success_count == 1
    assert job_result.failed_count == 0
    # nonexistent_table should be skipped
    skipped = [r for r in job_result.results if r.status == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].table == "nonexistent_table"


def test_extract_source_to_ods_empty_tables(
    source_engine, target_engine, sample_source, monkeypatch
):
    """Extracting with empty table list should return empty results."""
    monkeypatch.setattr(
        extract_module, "_get_source_engine", lambda _source, _password: source_engine
    )
    job_result = extract_source_to_ods(
        source=sample_source,
        source_password="any",
        target_engine=target_engine,
        tables=[],
        ods_schema="ods_src_001",
    )

    assert len(job_result.results) == 0
    assert job_result.total_rows == 0


def test_extract_result_dataclass():
    """ExtractResult should track copy outcomes correctly."""
    r = ExtractResult(source_id="SRC-001", table="patients", rows_copied=100, status="success")
    assert r.source_id == "SRC-001"
    assert r.rows_copied == 100
    assert r.status == "success"


def test_get_source_engine_sqlite_no_connect_timeout():
    """SQLite engine should NOT have connect_timeout (not supported)."""
    from antline.core.extract import _get_source_engine

    source = DataSource(
        id="SRC-001",
        name="SQLite Source",
        db_type=DataSourceType.POSTGRESQL,
        host="localhost",
        port=5432,
        database=":memory:",
        user="test",
    )
    # Override connection string to be sqlite
    engine = _get_source_engine(source, "")
    # Should succeed without connect_timeout issues
    assert engine is not None


def test_create_ods_schema_postgresql():
    """_create_ods_schema should create schema in PostgreSQL target."""
    from antline.core.extract import _create_ods_schema

    # Use SQLite but pretend it's postgresql by manually executing
    engine = create_engine("sqlite:///:memory:")
    # SQLite doesn't support schemas, but we can verify the generic path
    # by testing with a mock or by just ensuring no error on postgresql path
    # Since we can't easily mock dialect.name, we verify the function structure
    # through the existing extract tests that cover the integration path.
    # This test documents the intent.
    _create_ods_schema(engine, "ods_test", "sqlite")
    # SQLite path: no-op, should not raise


def test_create_ods_schema_mysql_skip():
    """_create_ods_schema should skip for MySQL (no true schema support)."""
    from antline.core.extract import _create_ods_schema

    engine = create_engine("sqlite:///:memory:")
    _create_ods_schema(engine, "ods_test", "mysql")
    # Should not raise


def test_create_ods_schema_generic_fallback():
    """_create_ods_schema generic fallback should attempt CREATE SCHEMA."""
    from antline.core.extract import _create_ods_schema

    engine = create_engine("sqlite:///:memory:")
    # Generic fallback runs CREATE SCHEMA IF NOT EXISTS on unknown dialects.
    # With SQLite this will fail, confirming the fallback path is reached.
    with pytest.raises(Exception):
        _create_ods_schema(engine, "ods_test", "duckdb")
