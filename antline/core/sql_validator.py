"""Validate generated model SQL against local target database.

Checks syntax, field types, and sample data by executing SQL
with dbt source() references resolved to actual local tables.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from antline.core.config import ProjectState

logger = logging.getLogger("antline.sql_validator")

_SOURCE_REF_PATTERN = re.compile(
    r"\{\{\s*source\s*\(\s*['\"](.+?)['\"]\s*,\s*['\"](.+?)['\"]\s*\)\s*\}\}"
)


def _get_target_engine(
    host: str, port: int, database: str, user: str, password: str
) -> Engine:
    conn_str = (
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    )
    return create_engine(
        conn_str,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "gssencmode": "disable"},
    )


def _parse_source_refs(sql: str) -> list[tuple[str, str]]:
    """Extract (source_id, table_name) tuples from dbt source() refs."""
    return list(set(_SOURCE_REF_PATTERN.findall(sql)))


def _resolve_source_table(
    state: ProjectState, sid: str, table_name: str, target_engine: Engine
) -> tuple[str | None, str]:
    """Resolve a source table to its local schema.table reference.

    Checks both FDW (foreign tables) and sync (physical tables) modes.

    Returns:
        (schema, status) where status is one of:
        "fdw", "sync", "missing", "not_postgresql"
    """
    source = state.get_source(sid)
    if not source:
        return None, "missing"

    if source.db_type.value != "postgresql":
        return None, "not_postgresql"

    # Check FDW mode: foreign table in schema = source.database
    fdw_schema = source.database
    try:
        with target_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.foreign_tables "
                    "WHERE foreign_table_schema = :schema AND foreign_table_name = :table"
                ),
                {"schema": fdw_schema, "table": table_name},
            )
            if result.fetchone():
                return fdw_schema, "fdw"
    except Exception:
        pass

    # Check sync mode: physical table in ods_<sid> schema
    sync_schema = f"ods_{sid.lower()}"
    try:
        with target_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": sync_schema, "table": table_name},
            )
            if result.fetchone():
                return sync_schema, "sync"
    except Exception:
        pass

    # Check if table exists in any schema (fallback)
    try:
        with target_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT table_schema FROM information_schema.tables "
                    "WHERE table_name = :table LIMIT 1"
                ),
                {"table": table_name},
            )
            row = result.fetchone()
            if row:
                return row[0], "unknown"
    except Exception:
        pass

    return None, "missing"


def _replace_source_refs(
    sql: str, state: ProjectState, target_engine: Engine
) -> tuple[str, dict[tuple[str, str], str]]:
    """Replace dbt source() refs with actual schema.table references.

    Returns:
        (replaced_sql, resolution_map)
        resolution_map: {(sid, table): status}
    """
    refs = _parse_source_refs(sql)
    resolution_map: dict[tuple[str, str], str] = {}
    replaced = sql

    for sid, table_name in refs:
        schema, status = _resolve_source_table(state, sid, table_name, target_engine)
        resolution_map[(sid, table_name)] = status

        if schema:
            # Replace {{ source('sid', 'table') }} with "schema"."table"
            pattern = re.compile(
                r"\{\{\s*source\s*\(\s*['\"]"
                + re.escape(sid)
                + r"['\"]\s*,\s*['\"]"
                + re.escape(table_name)
                + r"['\"]\s*\)\s*\}\}"
            )
            replaced = pattern.sub(f'"{schema}"."{table_name}"', replaced)

    return replaced, resolution_map


def validate_sql(
    model_name: str,
    sql: str,
    state: ProjectState,
    target_engine: Engine,
) -> dict[str, Any]:
    """Validate a single model SQL against the local target database.

    Steps:
    1. Replace dbt source() refs with local schema.table
    2. Run EXPLAIN to check syntax
    3. Run LIMIT 1 query to get column names and sample row

    Returns a dict with validation results.
    """
    result: dict[str, Any] = {
        "model_name": model_name,
        "success": False,
        "syntax_ok": False,
        "columns": [],
        "sample_row": None,
        "row_count_estimate": None,
        "resolution": {},
        "message": "",
        "error": "",
    }

    # Step 1: Replace source refs
    resolved_sql, resolution_map = _replace_source_refs(sql, state, target_engine)
    result["resolution"] = {f"{sid}.{tbl}": status for (sid, tbl), status in resolution_map.items()}

    # Check if all sources are available
    missing = [k for k, v in resolution_map.items() if v == "missing"]
    if missing:
        missing_str = ", ".join(f"{sid}.{tbl}" for sid, tbl in missing)
        result["message"] = f"源表未在本地接入: {missing_str}"
        result["error"] = "Run `antline source setup` first to make source tables available locally."
        return result

    not_pg = [k for k, v in resolution_map.items() if v == "not_postgresql"]
    if not_pg:
        pg_str = ", ".join(f"{sid}.{tbl}" for sid, tbl in not_pg)
        result["message"] = f"非 PostgreSQL 源暂不支持校验: {pg_str}"
        return result

    # Step 2: EXPLAIN to validate syntax
    try:
        with target_engine.connect() as conn:
            explain_sql = f"EXPLAIN (FORMAT JSON) {resolved_sql}"
            explain_result = conn.execute(text(explain_sql))
            explain_data = explain_result.scalar()
            result["syntax_ok"] = True
            # Try to extract row count estimate from EXPLAIN
            try:
                if explain_data and isinstance(explain_data, list):
                    plan = explain_data[0].get("Plan", {})
                    result["row_count_estimate"] = plan.get("Plan Rows")
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)
        result["message"] = f"SQL 语法校验失败: {exc}"
        return result

    # Step 3: LIMIT 1 to get columns and sample row
    try:
        with target_engine.connect() as conn:
            test_sql = f"SELECT * FROM ({resolved_sql}) AS _antline_validate LIMIT 1"
            test_result = conn.execute(text(test_sql))
            columns = list(test_result.keys())
            result["columns"] = columns
            row = test_result.fetchone()
            if row:
                result["sample_row"] = {
                    k: (str(v) if v is not None else None)
                    for k, v in dict(row._mapping).items()
                }
    except Exception as exc:
        result["error"] = str(exc)
        result["message"] = f"SQL 执行校验失败: {exc}"
        return result

    result["success"] = True
    result["message"] = f"校验通过 | 字段: {len(result['columns'])} 个"
    if result["row_count_estimate"] is not None:
        result["message"] += f" | 预估行数: {result['row_count_estimate']:,}"
    return result


def validate_all_model_sqls(
    state: ProjectState,
    model_sqls: dict[str, str],
    target_user: str,
    target_password: str,
    target_db: str | None = None,
) -> dict[str, Any]:
    """Validate all model SQLs for a requirement.

    Returns a summary dict with per-model validation results.
    """
    platform = state.workspace_platform()
    if not platform:
        return {"error": "Workspace platform not configured"}

    db_type = platform.get("db_type", "postgresql")
    if db_type != "postgresql":
        return {"error": f"SQL validation requires PostgreSQL target, got {db_type}"}

    host = platform.get("host", "localhost")
    port = platform.get("port", 5432)
    database = target_db or platform.get("database", "")
    if not database:
        return {"error": "Target database not configured"}

    try:
        target_engine = _get_target_engine(
            host, port, database, target_user, target_password
        )
        # Test connection
        with target_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return {"error": f"Cannot connect to target database: {exc}"}

    results: dict[str, Any] = {}
    for model_name, sql in model_sqls.items():
        results[model_name] = validate_sql(
            model_name, sql, state, target_engine
        )

    return results
