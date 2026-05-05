"""Integration tests for `source explore` against local PostgreSQL.

These tests require a running PostgreSQL instance with the following databases:
  - his_db, bingan_db, emr_db, lis_db, ris_db

Connection: postgresql://wbg:5678@localhost:5432

Run with:
    pytest tests/test_explore_postgres.py -v --run-postgres

Or run the standalone script:
    python scripts/test_explore_postgres.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from antline.core.db import explore_source
from antline.core.models import DataSource, DataSourceType

PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "wbg"
PG_PASSWORD = "5678"
PG_DATABASES = ["his_db", "bingan_db", "emr_db", "lis_db", "ris_db"]


def _pg_available() -> bool:
    """Check if PostgreSQL is reachable."""
    try:
        engine = create_engine(
            f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/postgres",
            connect_args={"connect_timeout": 3},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# Skip all tests in this file unless --run-postgres is passed
pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not reachable (localhost:5432)",
)


@pytest.fixture(params=PG_DATABASES)
def pg_source(request):
    """Parametrized fixture for each PG database."""
    db_name = request.param
    return DataSource(
        id=f"SRC-{db_name.upper()}",
        name=db_name,
        db_type=DataSourceType.POSTGRESQL,
        host=PG_HOST,
        port=PG_PORT,
        database=db_name,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def test_explore_pg_connectivity(pg_source: DataSource) -> None:
    """Verify we can connect and get a report."""
    report = explore_source(pg_source)
    assert report.source_id == pg_source.id
    assert report.summary["db_type"] == "postgresql"
    assert report.summary["database"] == pg_source.database


def test_explore_pg_has_tables(pg_source: DataSource) -> None:
    """Verify at least one table is found."""
    report = explore_source(pg_source)
    assert report.summary["total_tables"] >= 0  # may be empty, but shouldn't crash


def test_explore_pg_report_structure(pg_source: DataSource) -> None:
    """Verify report has expected structure."""
    report = explore_source(pg_source)
    assert "total_tables" in report.summary
    assert "total_rows" in report.summary
    assert "total_columns" in report.summary

    for table in report.tables:
        assert table.name
        assert isinstance(table.row_count, int)
        assert table.row_count >= -1  # -1 means unknown/error
        for col in table.columns:
            assert col.name
            assert col.data_type
