#!/usr/bin/env python3
"""Integration test: explore local PostgreSQL databases.

Usage:
    cd /Users/wbg/works/Antline
    python scripts/test_explore_postgres.py

Databases: his_db, bingan_db, emr_db, lis_db, ris_db
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from antline.core.db import explore_source
from antline.core.models import DataSource, DataSourceType

DATABASES = ["his_db", "bingan_db", "emr_db", "lis_db", "ris_db"]
HOST = "localhost"
PORT = 5432
USER = "wbg"
PASSWORD = "5678"


def test_connection(db_name: str) -> bool:
    """Quick connectivity test."""
    from sqlalchemy import create_engine, text

    conn_str = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{db_name}"
    try:
        engine = create_engine(conn_str, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"  [FAIL] {db_name}: {e}")
        return False


def explore_database(db_name: str) -> None:
    """Run explore_source on a database and print summary."""
    source = DataSource(
        id=f"SRC-{db_name.upper()}",
        name=db_name,
        db_type=DataSourceType.POSTGRESQL,
        host=HOST,
        port=PORT,
        database=db_name,
        user=USER,
        password=PASSWORD,
    )

    print(f"\n[EXPLORING] {db_name} ...")
    report = explore_source(source)

    print(f"  Tables: {report.summary['total_tables']}")
    print(f"  Total rows: {report.summary['total_rows']:,}")
    print(f"  Total columns: {report.summary['total_columns']}")

    # Top 5 tables by row count
    sorted_tables = sorted(report.tables, key=lambda t: t.row_count, reverse=True)[:5]
    print(f"  Top tables:")
    for t in sorted_tables:
        pk = ", ".join(t.primary_key) or "-"
        print(f"    - {t.name}: {t.row_count:,} rows, {len(t.columns)} cols, PK=[{pk}]")

    # Save report
    import yaml

    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    out_path = report_dir / f"{source.id}_explore.yml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(report.model_dump(mode="json"), f, allow_unicode=True)
    print(f"  Report saved: {out_path}")


def main() -> int:
    print("=" * 60)
    print("Antline PostgreSQL Explore Integration Test")
    print(f"Host: {HOST}:{PORT} | User: {USER}")
    print("=" * 60)

    # Connectivity check
    print("\n[1/2] Connectivity check...")
    reachable = []
    for db in DATABASES:
        if test_connection(db):
            reachable.append(db)
            print(f"  [OK]   {db}")

    if not reachable:
        print("\n[ERROR] No databases reachable. Is PostgreSQL running?")
        return 1

    # Explore
    print(f"\n[2/2] Exploring {len(reachable)} database(s)...")
    for db in reachable:
        try:
            explore_database(db)
        except Exception as e:
            print(f"  [ERROR] {db}: {e}")

    print("\n[COMPLETE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
