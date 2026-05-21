"""Extract job: sync source tables into target database ODS layer."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Column, Inspector, MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine

from antline.core.models import DataSource

logger = logging.getLogger("antline.extract")


@dataclass
class ExtractResult:
    """Result of extracting a single table."""

    source_id: str = ""
    table: str = ""
    rows_copied: int = 0
    status: Literal["success", "failed", "skipped"] = "success"
    message: str = ""


@dataclass
class ExtractJobResult:
    """Result of a complete extract job for a project."""

    source_id: str = ""
    ods_schema: str = ""
    results: list[ExtractResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(r.rows_copied for r in self.results if r.status == "success")

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


def _get_source_engine(source: DataSource, password: str) -> Engine:
    """Create engine for source database."""
    kwargs: dict = {"pool_pre_ping": True}
    conn_str = source.connection_string(password)
    if "sqlite" not in conn_str:
        kwargs["connect_args"] = {"connect_timeout": 10}
    return create_engine(conn_str, **kwargs)


def _build_target_table(
    inspector: Inspector,
    table_name: str,
    schema: str | None,
    target_metadata: MetaData,
) -> Table:
    """Build a Table object from inspector column info.

    ODS layer tables are created without constraints (no PK, FK, unique,
    defaults, or auto-increment) to maximise compatibility and avoid
    insertion failures from source data inconsistencies.
    """
    try:
        columns_info = inspector.get_columns(table_name, schema=schema)
    except Exception as exc:
        raise ValueError(f"Table '{table_name}' not found in source") from exc

    cols: list[Column[Any]] = []
    for col_info in columns_info:
        col = Column(
            col_info["name"],
            col_info["type"],
            nullable=True,  # ODS: all columns nullable for safety
        )
        cols.append(col)

    return Table(table_name, target_metadata, *cols)


def _copy_data(
    source_engine: Engine,
    target_engine: Engine,
    source_table: Table,
    target_table: Table,
    batch_size: int = 10000,
) -> int:
    """Copy data from source table to target table.

    Returns the number of rows copied.

    Uses server-side streaming when the driver supports it (PostgreSQL,
    MySQL) and falls back to client-side iteration for SQLite and others.
    """
    rows_copied = 0

    # Try server-side streaming first
    try:
        with source_engine.connect().execution_options(stream_results=True) as src_conn:
            result = src_conn.execute(source_table.select())
            for partition in result.partitions(batch_size):
                rows: list[dict[str, Any]] = [
                    dict(row._mapping)
                    for row in partition  # type: ignore[arg-type]
                ]
                if rows:
                    with target_engine.begin() as tgt_conn:
                        tgt_conn.execute(target_table.insert(), rows)
                    rows_copied += len(rows)
        return rows_copied
    except Exception:
        logger.debug("Server-side streaming not available, using fallback")

    # Fallback: client-side iteration
    with source_engine.connect() as src_conn:
        result = src_conn.execute(source_table.select())
        batch: list[dict[str, Any]] = []
        for row in result:
            batch.append(dict(row._mapping))  # type: ignore[arg-type]
            if len(batch) >= batch_size:
                with target_engine.begin() as tgt_conn:
                    tgt_conn.execute(target_table.insert(), batch)
                rows_copied += len(batch)
                batch = []
        if batch:
            with target_engine.begin() as tgt_conn:
                tgt_conn.execute(target_table.insert(), batch)
            rows_copied += len(batch)

    return rows_copied


def extract_source_to_ods(
    source: DataSource,
    source_password: str,
    target_engine: Engine,
    tables: list[str],
    ods_schema: str,
    source_schema: str | None = None,
    batch_size: int = 10000,
) -> ExtractJobResult:
    """Extract listed tables from a source into the target ODS schema.

    Creates the ODS schema if it does not exist, drops and recreates each
    target table, then copies data in batches.
    """
    job_result = ExtractJobResult(source_id=source.id, ods_schema=ods_schema)

    # Resolve source schema from DataSource if not provided
    if source_schema is None:
        source_schema = source.database if source.db_type.value in ("mysql", "tidb") else None

    source_engine = _get_source_engine(source, source_password)

    # Determine effective target schema based on dialect capabilities
    target_dialect = target_engine.dialect.name
    if target_dialect == "sqlite":
        # SQLite does not support schemas; tables go into 'main'
        effective_ods_schema: str | None = None
    else:
        effective_ods_schema = ods_schema

    # Create ODS schema in target database (no-op for SQLite/MySQL)
    _create_ods_schema(target_engine, ods_schema, target_dialect)

    inspector = inspect(source_engine)

    for table_name in tables:
        result = ExtractResult(source_id=source.id, table=table_name)

        # Check source table exists
        available = inspector.get_table_names(schema=source_schema)
        if table_name not in available:
            result.status = "skipped"
            result.message = f"Table not found in source (available: {available})"
            job_result.results.append(result)
            continue

        try:
            # Build target table definition
            target_metadata = MetaData(schema=effective_ods_schema)
            target_table = _build_target_table(
                inspector, table_name, source_schema, target_metadata
            )

            # Drop existing table if present
            target_table.drop(target_engine, checkfirst=True)

            # Create table in target database
            target_table.create(target_engine)

            # Build source table for querying
            source_metadata = MetaData(schema=source_schema)
            source_tbl = Table(table_name, source_metadata, autoload_with=source_engine)

            # Copy data
            rows = _copy_data(source_engine, target_engine, source_tbl, target_table, batch_size)
            result.rows_copied = rows
            result.status = "success"

        except Exception as exc:
            result.status = "failed"
            result.message = str(exc)
            logger.error("Extract failed for %s.%s: %s", source.id, table_name, exc)

        job_result.results.append(result)

    return job_result


def _create_ods_schema(engine: Engine, schema_name: str, dialect_name: str) -> None:
    """Create schema in target database if it does not exist."""
    if dialect_name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    elif dialect_name in ("mysql", "mariadb"):
        # MySQL does not have true schemas; CREATE SCHEMA == CREATE DATABASE.
        # We skip schema creation for MySQL and use the database name directly.
        pass
    elif dialect_name == "sqlite":
        # SQLite does not support schemas natively; tables are created in main.
        pass
    else:
        # Generic fallback
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
