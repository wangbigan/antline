"""Database connection and metadata reflection."""

from __future__ import annotations

import re
import warnings
from typing import Any

from sqlalchemy import (
    Inspector,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine

from antline.core.models import (
    ColumnMeta,
    DataSource,
    FieldStats,
    SourceExploreReport,
    TableMeta,
)
from antline.core.pii_detector import field_has_pii, mask_value

# Suppress SQLAlchemy 2.0 warnings for reflection
warnings.filterwarnings("ignore", category=DeprecationWarning)


def get_engine(source: DataSource, password: str = "") -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    conn_str = source.connection_string(password)
    # SQLite does not support connect_timeout
    if "sqlite" not in conn_str:
        kwargs["connect_args"] = {"connect_timeout": 10}
    return create_engine(conn_str, **kwargs)


def _get_table_row_count(engine: Engine, table_name: str, schema_name: str | None) -> int:
    try:
        schema_clause = f'"{schema_name}".' if schema_name else ""
        sql = text(f'SELECT COUNT(*) FROM {schema_clause}"{table_name}"')
        with engine.connect() as conn:
            result = conn.execute(sql)
            return result.scalar() or 0
    except Exception:
        return -1


def _get_column_stats(
    engine: Engine, table_name: str, schema_name: str | None, col_name: str
) -> FieldStats:
    stats = FieldStats()
    schema_clause = f'"{schema_name}".' if schema_name else ""
    quoted_table = f'{schema_clause}"{table_name}"'

    try:
        with engine.connect() as conn:
            # null count
            null_result = conn.execute(
                text(f'SELECT COUNT(*) FROM {quoted_table} WHERE "{col_name}" IS NULL')
            )
            stats.null_count = null_result.scalar() or 0

            # unique count
            unique_result = conn.execute(
                text(f'SELECT COUNT(DISTINCT "{col_name}") FROM {quoted_table}')
            )
            stats.unique_count = unique_result.scalar() or 0

            # min/max for string/date/number columns
            try:
                minmax_result = conn.execute(
                    text(f'SELECT MIN("{col_name}"), MAX("{col_name}") FROM {quoted_table}')
                )
                row = minmax_result.fetchone()
                if row:
                    stats.min_value = str(row[0]) if row[0] is not None else None
                    stats.max_value = str(row[1]) if row[1] is not None else None
            except Exception:
                pass

            # top 5 values
            try:
                topn_result = conn.execute(
                    text(
                        f'SELECT "{col_name}", COUNT(*) as cnt '
                        f"FROM {quoted_table} "
                        f'GROUP BY "{col_name}" '
                        f"ORDER BY cnt DESC "
                        f"LIMIT 5"
                    )
                )
                stats.topn_values = [
                    {"value": str(v), "count": c} for v, c in topn_result.fetchall()
                ]
            except Exception:
                pass
    except Exception:
        pass

    return stats


def _get_sample_data(
    engine: Engine, table_name: str, schema_name: str | None, col_name: str, limit: int = 5
) -> list[Any]:
    schema_clause = f'"{schema_name}".' if schema_name else ""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    f'SELECT DISTINCT "{col_name}" '
                    f'FROM {schema_clause}"{table_name}" '
                    f'WHERE "{col_name}" IS NOT NULL '
                    f"LIMIT {limit}"
                )
            )
            return [row[0] for row in result.fetchall()]
    except Exception:
        return []


# Field name tokens that indicate sensitive data (exact word match)
_SENSITIVE_WORDS = (
    "name",
    "phone",
    "tel",
    "mobile",
    "email",
    "mail",
    "card",
    "identity",
    "address",
    "addr",
    "password",
    "passwd",
    "pwd",
    "ssn",
    "social",
    "bank",
    "account",
)

# Chinese sensitive substrings
_SENSITIVE_CHINESE = (
    "姓名",
    "电话",
    "手机",
    "邮箱",
    "身份证",
    "地址",
    "住址",
    "密码",
    "卡号",
    "账号",
)

# Prefixes that make "name" non-sensitive (e.g. group_name, type_name)
_NON_SENSITIVE_PREFIXES = ("group", "type", "class", "schema", "table", "user")


def _is_sensitive(col_name: str) -> bool:
    """Check if column name indicates sensitive data. Exact word match to avoid false positives (e.g. 'gender' -> 'id')."""
    lower = col_name.lower()
    # Split by separators
    tokens = re.split(r"[_\-\s]+", lower)

    for i, t in enumerate(tokens):
        # Direct token match
        if t in _SENSITIVE_WORDS:
            if t == "name" and i > 0 and tokens[i - 1] in _NON_SENSITIVE_PREFIXES:
                continue
            return True

        # CamelCase split within token
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower().split()
        for j, p in enumerate(parts):
            if p in _SENSITIVE_WORDS:
                if p == "name" and j > 0 and parts[j - 1] in _NON_SENSITIVE_PREFIXES:
                    continue
                return True

    # Chinese patterns use substring match
    for c in _SENSITIVE_CHINESE:
        if c in lower:
            return True
    return False


def explore_source(
    source: DataSource,
    password: str = "",
    max_tables: int = 0,
    mask_sensitive: bool = True,
) -> SourceExploreReport:
    """Explore a data source and return metadata + statistics."""
    engine = get_engine(source, password)
    inspector: Inspector = inspect(engine)

    report = SourceExploreReport(source_id=source.id)
    total_rows = 0
    total_columns = 0
    table_count = 0

    schemas = inspector.get_schema_names() if hasattr(inspector, "get_schema_names") else [None]
    # Filter out system schemas for PostgreSQL
    if source.db_type.value == "postgresql":
        schemas = [s for s in schemas if s not in ("information_schema", "pg_catalog")]

    for schema_name in schemas:
        try:
            table_names = inspector.get_table_names(schema=schema_name)
        except Exception:
            continue

        for table_name in table_names:
            if max_tables > 0 and table_count >= max_tables:
                break

            table_count += 1
            row_count = _get_table_row_count(engine, table_name, schema_name)
            total_rows += max(row_count, 0)

            columns: list[ColumnMeta] = []
            try:
                col_info = inspector.get_columns(table_name, schema=schema_name)
            except Exception:
                col_info = []

            pk_cols: list[str] = []
            try:
                pk = inspector.get_pk_constraint(table_name, schema=schema_name)
                pk_cols = pk.get("constrained_columns", []) if pk else []
            except Exception:
                pass

            # Get table comment
            table_comment = None
            try:
                tc = inspector.get_table_comment(table_name, schema=schema_name)
                if tc:
                    table_comment = tc.get("text")
            except Exception:
                pass

            for col in col_info:
                total_columns += 1
                col_name = col["name"]
                stats = _get_column_stats(engine, table_name, schema_name, col_name)
                sample = _get_sample_data(engine, table_name, schema_name, col_name)

                # Mask sensitive sample data: by field name OR by value-based PII detection
                if mask_sensitive:
                    name_sensitive = _is_sensitive(col_name)
                    pii_types = field_has_pii(sample, col_name)
                    value_sensitive = bool(pii_types)
                    if name_sensitive or value_sensitive:
                        sample = [
                            mask_value(v, pii_types=pii_types, col_name=col_name) for v in sample
                        ]

                if row_count > 0:
                    stats.null_rate = round(stats.null_count / row_count, 4)
                    stats.is_unique_candidate = stats.unique_count == row_count

                columns.append(
                    ColumnMeta(
                        name=col_name,
                        data_type=str(col.get("type", "unknown")),
                        nullable=col.get("nullable", True),
                        default=str(col["default"]) if col.get("default") else None,
                        max_length=col.get("max_length"),
                        numeric_precision=col.get("precision"),
                        numeric_scale=col.get("scale"),
                        comment=col.get("comment"),
                        stats=stats,
                        sample_data=sample,
                    )
                )

            report.tables.append(
                TableMeta(
                    name=table_name,
                    schema_name=schema_name,
                    row_count=row_count,
                    comment=table_comment,
                    columns=columns,
                    primary_key=pk_cols,
                )
            )

        if max_tables > 0 and table_count >= max_tables:
            break

    report.summary = {
        "database": source.database,
        "db_type": source.db_type.value,
        "total_tables": table_count,
        "total_rows": total_rows,
        "total_columns": total_columns,
    }

    return report
