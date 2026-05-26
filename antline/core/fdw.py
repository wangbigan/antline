"""Foreign Data Wrapper (FDW) setup for PostgreSQL sources.

Creates FDW servers, user mappings, and imports foreign schemas
so that local PostgreSQL can query remote tables directly.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Inspector, create_engine, inspect, text
from sqlalchemy.engine import Engine

from antline.core.models import DataSource

logger = logging.getLogger("antline.fdw")


def _get_target_engine(
    host: str, port: int, database: str, user: str, password: str
) -> Engine:
    """Create engine for local target PostgreSQL database."""
    conn_str = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    return create_engine(
        conn_str,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "gssencmode": "disable"},
    )


def setup_fdw_for_source(
    source: DataSource,
    source_password: str,
    target_host: str,
    target_port: int,
    target_database: str,
    target_user: str,
    target_password: str,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """Set up FDW for a single PostgreSQL source in the target database.

    Creates (if not exists):
    - postgres_fdw extension
    - FDW server pointing to the source
    - User mapping for the target user
    - Local schema named after the source database
    - Foreign tables imported from the source

    Args:
        source: The remote data source (must be postgresql).
        source_password: Password for the remote source database.
        target_host: Local target PostgreSQL host.
        target_port: Local target PostgreSQL port.
        target_database: Local target PostgreSQL database name.
        target_user: Local target PostgreSQL user.
        target_password: Local target PostgreSQL password.
        tables: Optional list of specific tables to import. If None,
            all tables from all non-system schemas are imported.

    Returns:
        A dict with ``success`` (bool), ``schema`` (str), ``tables`` (list),
        and ``message`` (str).
    """
    if source.db_type.value != "postgresql":
        return {
            "success": False,
            "schema": "",
            "tables": [],
            "message": f"FDW only supports PostgreSQL sources, got {source.db_type.value}",
        }

    target_engine = _get_target_engine(
        target_host, target_port, target_database, target_user, target_password
    )

    server_name = f"fdw_{source.id.lower().replace('-', '_')}_server"
    local_schema = source.database  # FDW schema name = source database name

    try:
        with target_engine.begin() as conn:
            # 1. Create extension
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgres_fdw"))

            # 2. Create server
            conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_foreign_server WHERE srvname = '{server_name}'
                        ) THEN
                            CREATE SERVER "{server_name}"
                                FOREIGN DATA WRAPPER postgres_fdw
                                OPTIONS (host '{source.host}', dbname '{source.database}', port '{source.port}');
                        END IF;
                    END $$;
                    """
                )
            )

            # 3. Create user mapping
            conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_user_mappings
                            WHERE srvname = '{server_name}' AND umuser = CURRENT_USER::regrole::oid
                        ) THEN
                            CREATE USER MAPPING FOR CURRENT_USER
                                SERVER "{server_name}"
                                OPTIONS (user '{source.user}', password '{source_password}');
                        END IF;
                    END $$;
                    """
                )
            )

            # 4. Create local schema
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{local_schema}"'))

        # 5. Determine foreign schemas to import
        # Connect to source to discover schemas
        source_engine = create_engine(
            source.connection_string(source_password),
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10, "gssencmode": "disable"},
        )
        inspector: Inspector = inspect(source_engine)

        schema_names = (
            inspector.get_schema_names()
            if hasattr(inspector, "get_schema_names")
            else ["public"]
        )
        # Filter out system schemas
        schema_names = [
            s for s in schema_names if s not in ("information_schema", "pg_catalog")
        ]

        imported_tables: list[str] = []

        with target_engine.begin() as conn:
            for fs in schema_names:
                if tables:
                    # Import specific tables only
                    for t in tables:
                        try:
                            conn.execute(
                                text(
                                    f'IMPORT FOREIGN SCHEMA "{fs}" LIMIT TO ("{t}") '
                                    f'FROM SERVER "{server_name}" INTO "{local_schema}"'
                                )
                            )
                            imported_tables.append(t)
                        except Exception as exc:
                            logger.warning(
                                "Failed to import table %s from schema %s: %s",
                                t,
                                fs,
                                exc,
                            )
                else:
                    # Import all tables from the schema
                    try:
                        conn.execute(
                            text(
                                f'IMPORT FOREIGN SCHEMA "{fs}" FROM SERVER "{server_name}" '
                                f'INTO "{local_schema}"'
                            )
                        )
                        # Query which tables were actually imported
                        result = conn.execute(
                            text(
                                "SELECT foreign_table_name FROM information_schema.foreign_tables "
                                "WHERE foreign_table_schema = :schema"
                            ),
                            {"schema": local_schema},
                        )
                        imported_tables.extend([row[0] for row in result.fetchall()])
                    except Exception as exc:
                        logger.warning(
                            "Failed to import schema %s: %s",
                            fs,
                            exc,
                        )

        # Deduplicate
        imported_tables = sorted(set(imported_tables))

        return {
            "success": True,
            "schema": local_schema,
            "tables": imported_tables,
            "message": f"FDW setup complete: {len(imported_tables)} table(s) in schema '{local_schema}'",
        }

    except Exception as exc:
        logger.error("FDW setup failed for %s: %s", source.id, exc)
        return {
            "success": False,
            "schema": local_schema,
            "tables": [],
            "message": str(exc),
        }
