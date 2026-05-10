"""Data project management commands."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from antline.core.config import ProjectState
from antline.core.git import git_add_all, git_commit
from antline.core.models import (
    FieldMapping,
    Project,
    ProjectStatus,
    ProjectVersion,
    QCRule,
    RequirementStatus,
    SourceExploreReport,
)

app = typer.Typer(no_args_is_help=True)
console = Console()


# ---------------------------------------------------------------------------
# Scaffold helpers
# ---------------------------------------------------------------------------


def _dbt_safe_name(name: str) -> str:
    """Convert a project name to a dbt-safe name: only [a-z0-9_], no leading digit."""
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    if safe and safe[0].isdigit():
        safe = "prj_" + safe
    if not safe:
        safe = "project"
    return safe


def _get_dbt_env(prj_id: str, platform: dict[str, Any] | None) -> dict[str, str]:
    """Build environment dict for dbt subprocess with password injected.

    Profiles.yml references passwords via env_var. This ensures the workspace
    platform password is available without a manual .env file.
    """
    env = dict(os.environ)
    if platform:
        password = platform.get("password", "")
        env_var_name = f"DBT_PASSWORD_{prj_id.replace('-', '_')}"
        env[env_var_name] = password
    return env


def _load_explore_reports(
    state: ProjectState, source_ids: list[str]
) -> dict[str, SourceExploreReport]:
    """Load explore reports for the given source IDs."""
    reports: dict[str, SourceExploreReport] = {}
    for sid in source_ids:
        report_path = state.root / "sources" / sid / "explore" / "report.yml"
        if report_path.exists():
            data = yaml.safe_load(report_path.read_text())
            if data:
                reports[sid] = SourceExploreReport.model_validate(data)
    return reports


def _collect_scaffold_data(
    project: Project, state: ProjectState
) -> tuple[
    dict[str, list[str]],  # source_id -> list of used table names
    dict[str, dict[str, list[FieldMapping]]],  # req_id -> {target_table: [mappings]}
]:
    """Collect tables and mappings needed for scaffold from assessments."""
    used_tables: dict[str, set[str]] = defaultdict(set)
    req_mappings: dict[str, dict[str, list[FieldMapping]]] = defaultdict(lambda: defaultdict(list))

    for req_id in project.requirement_ids:
        req = state.get_requirement(req_id)
        if not req or not req.assessment:
            continue

        assessment = req.assessment
        for m in assessment.field_mappings:
            if m.source_table:
                # Determine which source this table belongs to
                for sid in assessment.source_ids:
                    reports = _load_explore_reports(state, [sid])
                    if sid in reports:
                        table_names = {t.name for t in reports[sid].tables}
                        if m.source_table in table_names:
                            used_tables[sid].add(m.source_table)
                            break

            target_table = m.target_field.split(".")[0]
            req_mappings[req_id][target_table].append(m)

    # Convert sets to sorted lists
    return {sid: sorted(list(tables)) for sid, tables in used_tables.items()}, dict(req_mappings)


def _ensure_target_database(
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    db_name: str,
) -> None:
    """Check if target database exists; create it and schemas if not.

    Raises:
        ValueError: If the database already exists.
    """
    from sqlalchemy import create_engine, text

    if db_type == "postgresql":
        admin_conn = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/postgres"
    elif db_type in ("mysql", "tidb"):
        admin_conn = f"mysql+pymysql://{user}:{password}@{host}:{port}/mysql"
    else:
        return  # SQLite or unsupported: nothing to do

    engine = create_engine(admin_conn, connect_args={"connect_timeout": 10})

    with engine.connect() as conn:
        if db_type == "postgresql":
            result = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            exists = result.fetchone() is not None
            if exists:
                raise ValueError(
                    f"Database '{db_name}' already exists. "
                    "Please choose a different name or delete it first."
                )
            conn.execute(text("COMMIT"))
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        elif db_type in ("mysql", "tidb"):
            result = conn.execute(
                text("SHOW DATABASES LIKE :name"),
                {"name": db_name},
            )
            exists = result.fetchone() is not None
            if exists:
                raise ValueError(
                    f"Database '{db_name}' already exists. "
                    "Please choose a different name or delete it first."
                )
            conn.execute(text(f"CREATE DATABASE `{db_name}`"))

    # Create schemas in the new database (PostgreSQL only)
    if db_type == "postgresql":
        target_conn = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
        target_engine = create_engine(target_conn)
        with target_engine.connect() as conn:
            for schema in ("row", "map", "clean"):
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.commit()


def _generate_dbt_project_yml(project: Project, dbt_dir: Path) -> Path:
    """Generate dbt_project.yml for the project."""
    dbt_dir.mkdir(parents=True, exist_ok=True)

    project_name = _dbt_safe_name(project.name)
    content = {
        "name": project_name,
        "version": "1.0.0",
        "config-version": 2,
        "profile": project_name,
        "model-paths": ["models"],
        "seed-paths": ["seeds"],
        "test-paths": ["tests"],
        "analysis-paths": ["analyses"],
        "macro-paths": ["macros"],
        "snapshot-paths": ["snapshots"],
        "target-path": "target",
        "clean-targets": ["target", "dbt_packages"],
        "models": {
            project_name: {
                "row": {"+materialized": "view", "+schema": "row"},
                "map": {"+materialized": "view", "+schema": "map"},
                "clean": {"+materialized": "table", "+schema": "clean"},
            }
        },
    }

    path = dbt_dir / "dbt_project.yml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(content, f, sort_keys=False, allow_unicode=True)

    # Generate custom schema name macro so +schema: row/map/clean
    # produces schemas literally named "row"/"map"/"clean" instead of "dev_row"/"dev_map"/"dev_clean".
    macro_dir = dbt_dir / "macros"
    macro_dir.mkdir(parents=True, exist_ok=True)
    (macro_dir / "generate_schema_name.sql").write_text(
        """{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
""",
        encoding="utf-8",
    )

    return path


def _generate_dbt_profile(
    project: Project,
    dbt_dir: Path,
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    db_name: str,
) -> Path:
    """Generate profiles.yml for the project."""
    profile_name = _dbt_safe_name(project.name)
    env_var_name = f"DBT_PASSWORD_{project.id.replace('-', '_')}"

    # dbt adapter type mapping
    dbt_type = {"postgresql": "postgres", "mysql": "mysql", "tidb": "mysql"}.get(db_type, db_type)

    profile = {
        profile_name: {
            "target": "dev",
            "outputs": {
                "dev": {
                    "type": dbt_type,
                    "host": host,
                    "port": port,
                    "user": user,
                    "password": f"{{{{ env_var('{env_var_name}', '') }}}}",
                    "dbname": db_name,
                    "schema": "dev",
                    "threads": 4,
                }
            },
        }
    }

    if dbt_type in ("mysql", "tidb"):
        profile[profile_name]["outputs"]["dev"]["schema"] = db_name

    path = dbt_dir / "profiles.yml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(profile, f, sort_keys=False, allow_unicode=True)
    return path


def _generate_sources_yml(
    project: Project,
    state: ProjectState,
    dbt_dir: Path,
    used_tables: dict[str, list[str]],
    source_mode: str = "fdw",
) -> None:
    """Generate sources.yml with actual tables from assessment mappings.

    PostgreSQL does not support cross-database references. Two modes:

    * ``fdw`` (default) – row layer queries FDW foreign tables inside the
      target database. ``schema`` points to the FDW schema (e.g. ``his_db``).
      No ``database`` key for PostgreSQL.

    * ``sync`` – row layer queries physical tables that have been synced into
      the target database ODS layer. ``schema`` points to the ODS schema
      (e.g. ``ods_src_001``). No ``database`` key for PostgreSQL.

    For MySQL/TiDB ``database == schema`` so both are set.
    """
    sources_yml_dir = dbt_dir / "models"
    sources_yml_dir.mkdir(parents=True, exist_ok=True)

    sources_config: dict = {"version": 2, "sources": []}

    for req_id in project.requirement_ids:
        req = state.get_requirement(req_id)
        if not req or not req.assessment:
            continue

        for sid in req.assessment.source_ids:
            src = state.get_source(sid)
            if not src:
                continue

            tables = used_tables.get(sid, [])
            if not tables:
                continue

            # Resolve schema
            if src.db_type.value == "postgresql":
                if source_mode == "fdw":
                    # FDW: local schema name = source database name (matches fdw_setup.sql)
                    schema = src.database
                else:  # sync
                    schema = f"ods_{sid.lower()}"
            else:
                # MySQL/TiDB: database == schema
                schema = src.database

            source_entry: dict[str, Any] = {
                "name": src.id,
                "schema": schema,
                "tables": [
                    {"name": t, "description": f"Source table from {src.name}"} for t in tables
                ],
            }

            if src.db_type.value in ("mysql", "tidb"):
                source_entry["database"] = src.database

            sources_config["sources"].append(source_entry)

    with open(sources_yml_dir / "sources.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump(sources_config, f, allow_unicode=True, sort_keys=False)


def _generate_fdw_script(
    project: Project,
    state: ProjectState,
    dbt_dir: Path,
    used_tables: dict[str, list[str]],
    target_db_type: str,
) -> Path | None:
    """Generate FDW setup SQL script for PostgreSQL sources.

    Returns the path to the generated script, or None if there are no
    PostgreSQL sources or the target is not PostgreSQL.
    """
    if target_db_type != "postgresql":
        return None

    lines: list[str] = [
        f"-- FDW setup for project {project.id}",
        "-- Run this script in the target database before `dbt build`",
        "",
        "CREATE EXTENSION IF NOT EXISTS postgres_fdw;",
        "",
    ]

    has_pg_source = False
    for req_id in project.requirement_ids:
        req = state.get_requirement(req_id)
        if not req or not req.assessment:
            continue

        for sid in req.assessment.source_ids:
            src = state.get_source(sid)
            if not src or src.db_type.value != "postgresql":
                continue

            tables = used_tables.get(sid, [])
            if not tables:
                continue

            has_pg_source = True

            # Collect unique foreign schemas from explore report
            reports = _load_explore_reports(state, [sid])
            foreign_schemas: set[str] = {"public"}
            if sid in reports:
                for t in reports[sid].tables:
                    if t.schema_name:
                        foreign_schemas.add(t.schema_name)

            # Server / schema names must be dbl-quoted if they contain hyphens.
            server_name = f"fdw_{sid.lower().replace('-', '_')}_server"
            local_schema = src.database  # FDW schema name = source database name
            q_server = f'"{server_name}"'
            q_schema = f'"{local_schema}"'

            lines.extend(
                [
                    f"-- Source: {sid} ({src.name}) -> {src.database}",
                    f"CREATE SERVER IF NOT EXISTS {q_server}",
                    "  FOREIGN DATA WRAPPER postgres_fdw",
                    f"  OPTIONS (host '{src.host}', dbname '{src.database}', port '{src.port}');",
                    f"CREATE USER MAPPING IF NOT EXISTS FOR CURRENT_USER SERVER {q_server}",
                    f"  OPTIONS (user '{src.user}', password '{src.password or ''}');",
                    f"CREATE SCHEMA IF NOT EXISTS {q_schema};",
                ]
            )
            for fs in sorted(foreign_schemas):
                q_fs = f'"{fs}"'
                lines.append(
                    f"IMPORT FOREIGN SCHEMA {q_fs} FROM SERVER {q_server} INTO {q_schema};"
                )
            lines.append("")

    if not has_pg_source:
        return None

    sql_dir = dbt_dir / "sql"
    sql_dir.mkdir(parents=True, exist_ok=True)
    path = sql_dir / "fdw_setup.sql"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _generate_row_models(
    project: Project, state: ProjectState, dbt_dir: Path, used_tables: dict[str, list[str]]
) -> None:
    """Generate row layer models: one per used source table."""
    models_dir = dbt_dir / "models" / "row"
    models_dir.mkdir(parents=True, exist_ok=True)

    # Clean old scaffolded models so stale files don't break dbt build
    for old in models_dir.glob("*.sql"):
        old.unlink()

    for sid, tables in used_tables.items():
        src = state.get_source(sid)
        if not src:
            continue

        for table_name in tables:
            safe_name = table_name.lower().replace(" ", "_")
            model_name = f"row_{safe_name}"

            sql = f"""-- Row layer: {table_name}
-- Source: {sid} ({src.name})

SELECT *
FROM {{{{ source('{sid}', '{table_name}') }}}}
"""
            (models_dir / f"{model_name}.sql").write_text(sql)


def _generate_map_models(
    project: Project,
    state: ProjectState,
    dbt_dir: Path,
    req_mappings: dict[str, dict[str, list[FieldMapping]]],
) -> None:
    """Generate map layer models from field mappings in assessment."""
    models_dir = dbt_dir / "models" / "map"
    models_dir.mkdir(parents=True, exist_ok=True)

    # Clean old scaffolded models so stale files don't break dbt build
    for old in models_dir.glob("*.sql"):
        old.unlink()

    for req_id, target_tables in req_mappings.items():
        req = state.get_requirement(req_id)
        if not req:
            continue

        for target_table, mappings in target_tables.items():
            model_name = f"map_{target_table}"

            # Build field SQL lines
            fields_sql: list[str] = []
            for m in mappings:
                target_field = m.target_field.split(".")[-1]

                if m.mapping_type == "direct" and m.source_field:
                    fields_sql.append(
                        f"    {m.source_field} AS {target_field},  -- direct from {m.source_table}"
                    )
                elif m.mapping_type == "transform" and m.source_field:
                    if m.transform_logic:
                        fields_sql.append(f"    -- TODO: {m.transform_logic}")
                        fields_sql.append(
                            f"    {m.source_field} AS {target_field},  -- transform from {m.source_table}"
                        )
                    else:
                        fields_sql.append(
                            f"    {m.source_field} AS {target_field},  -- TODO: transform from {m.source_table}"
                        )
                elif m.mapping_type == "merge":
                    fields_sql.append(
                        f"    NULL AS {target_field},  -- TODO: merge from multiple sources"
                    )
                else:  # missing or unmapped
                    fields_sql.append(f"    NULL AS {target_field},  -- missing: no source mapping")

            # Remove trailing comma from the last field line so "FROM" doesn't follow ","
            if fields_sql:
                fields_sql[-1] = re.sub(r",\s+--", " --", fields_sql[-1])

            # Determine primary source table (the one with most direct mappings)
            table_counts: dict[str | None, int] = defaultdict(int)
            for m in mappings:
                if m.source_table and m.mapping_type in ("direct", "transform"):
                    table_counts[m.source_table] += 1

            if table_counts:
                primary_table = max(table_counts, key=lambda k: table_counts[k])
                from_ref = f"{{{{ ref('row_{primary_table.lower().replace(' ', '_')}') }}}}"

                # If multiple tables contribute, add CTE hints
                other_tables = [t for t in table_counts if t != primary_table]
                cte_hints = ""
                if other_tables:
                    cte_lines = "\n".join(f"--   - {t}: JOIN or UNION needed" for t in other_tables)
                    cte_hints = f"""
-- NOTE: This target table requires data from multiple source tables.
-- Additional sources to integrate:
{cte_lines}
"""
            else:
                from_ref = "-- TODO: specify source reference"
                cte_hints = ""

            sql = f"""-- Map layer: {target_table}
-- Requirement: {req_id}
{cte_hints}
SELECT
{chr(10).join(fields_sql)}
FROM {from_ref}
"""
            (models_dir / f"{model_name}.sql").write_text(sql)


def _generate_clean_models(project: Project, state: ProjectState, dbt_dir: Path) -> None:
    """Generate clean layer model templates from target schemas."""
    models_dir = dbt_dir / "models" / "clean"
    models_dir.mkdir(parents=True, exist_ok=True)

    # Clean old scaffolded models so stale files don't break dbt build
    for old in models_dir.glob("*.sql"):
        old.unlink()

    for req_id in project.requirement_ids:
        req = state.get_requirement(req_id)
        if not req or not req.target_schemas:
            continue

        for schema in req.target_schemas:
            model_name = f"clean_{schema.table}"

            # Build explicit field list with type/constraint/comment annotations
            field_lines: list[str] = []
            for f in schema.fields:
                null_str = "nullable" if f.nullable else "NOT NULL"
                comment_part = f": {f.description}" if f.description else ""
                field_lines.append(f"    {f.name},     -- {f.data_type} {null_str}{comment_part}")

            # Remove trailing comma from last field so "FROM" doesn't follow ","
            if field_lines:
                field_lines[-1] = re.sub(r",\s+--", " --", field_lines[-1])

            fields_block = "\n".join(field_lines)

            sql = f"""-- Clean layer: {schema.table}
-- Requirement: {req_id}
--
-- TODO: Add data cleaning logic:
--   - Type casting to target schema
--   - Null handling for required fields
--   - Deduplication
--   - Business rule validation
--

SELECT
{fields_block}
FROM {{{{ ref('map_{schema.table}') }}}}
"""
            (models_dir / f"{model_name}.sql").write_text(sql)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Project name"),
    requirements: list[str] = typer.Option(
        ..., "--requirement", "-r", help="Requirement ID(s) to include"
    ),
    description: str = typer.Option("", "--description", "-d"),
    prj_id: str = typer.Option("", "--id", help="Custom project ID"),
) -> None:
    """Create a new project (initiate from approved requirements)."""
    state = ProjectState()

    # Validate requirements
    req_objs = []
    for rid in requirements:
        req = state.get_requirement(rid)
        if not req:
            console.print(f"[red]Requirement not found:[/] {rid}")
            raise typer.Exit(1)
        if req.status not in (RequirementStatus.APPROVED, RequirementStatus.IN_PROJECT):
            console.print(
                f"[red]Requirement {rid} not approved. Current status: {req.status.value}[/]"
            )
            raise typer.Exit(1)
        req_objs.append(req)

    pid = prj_id or state.next_project_id()

    # Build solution draft from requirements
    solution_lines = [f"## {name}", ""]
    for req in req_objs:
        solution_lines.append(f"### {req.id}: {req.name}")
        for schema in req.target_schemas:
            solution_lines.append(f"- Target table: `{schema.table}`")
            solution_lines.append(f"- Fields: {len(schema.fields)}")
        solution_lines.append("")

    # Build QC rules from target schemas
    qc_rules: list[QCRule] = []
    for req in req_objs:
        for schema in req.target_schemas:
            fields = schema.fields
            qc = QCRule(
                table=schema.table,
                null_checks=[f.name for f in fields if not f.nullable],
                unique_checks=[f.name for f in fields if "unique" in f.constraints],
            )
            qc_rules.append(qc)

    prj = Project(
        id=pid,
        name=name,
        description=description,
        requirement_ids=list(requirements),
        solution_draft="\n".join(solution_lines),
        qc_rules=qc_rules,
    )
    state.save_project(prj)

    # Mark requirements as in_project
    for req in req_objs:
        if req.status != RequirementStatus.IN_PROJECT:
            req.status = RequirementStatus.IN_PROJECT
            state.save_requirement(req)

    git_add_all(state.root)
    git_commit(f"feat(project): create {pid} with {len(requirements)} requirement(s)", state.root)

    console.print(f"[green]Created project:[/] {pid} — {name}")
    console.print(f"  Requirements: {', '.join(requirements)}")
    console.print("  Solution draft saved in project file")


@app.command("list")
def cmd_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all projects."""
    state = ProjectState()
    prjs = state.list_projects()

    if json_output:
        console.print(yaml.safe_dump([p.model_dump(mode="json") for p in prjs], allow_unicode=True))
        return

    if not prjs:
        console.print("[dim]No projects yet. Run `antline project create`.[/dim]")
        return

    table = Table("ID", "Name", "Status", "Requirements")
    for p in prjs:
        table.add_row(p.id, p.name, p.status.value, ", ".join(p.requirement_ids))
    console.print(table)


@app.command()
def show(
    prj_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Show project details."""
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    console.print(f"[bold]{prj.id}[/]  [dim]{prj.status.value}[/]")
    console.print(f"  Name: {prj.name}")
    if prj.description:
        console.print(f"  Description: {prj.description}")
    console.print(f"  Requirements: {', '.join(prj.requirement_ids)}")

    if prj.qc_rules:
        console.print("\n  QC Rules:")
        for qc in prj.qc_rules:
            console.print(f"    Table: {qc.table}")
            if qc.null_checks:
                console.print(f"      Not null: {', '.join(qc.null_checks)}")
            if qc.unique_checks:
                console.print(f"      Unique: {', '.join(qc.unique_checks)}")

    if prj.versions:
        console.print("\n  Versions:")
        for v in prj.versions:
            status = "[green]passed[/]" if v.passed else "[red]failed[/]"
            console.print(f"    {v.id} — {status}")


def _validate_db_credentials(
    db_type: str, host: str, port: int, user: str, password: str, db_name: str | None = None
) -> None:
    """Validate database credentials by attempting a connection.

    If db_name is provided, connect directly to it. Otherwise connect to
    the admin database (postgres/mysql) to verify credentials only.
    """
    from sqlalchemy import create_engine, text

    if db_type == "postgresql":
        target = db_name or "postgres"
        conn_str = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{target}"
    elif db_type in ("mysql", "tidb"):
        target = db_name or "mysql"
        conn_str = f"mysql+pymysql://{user}:{password}@{host}:{port}/{target}"
    else:
        return

    try:
        engine = create_engine(conn_str, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise ConnectionError(
            f"Could not connect to {db_type} at {host}:{port} "
            f"(user={user}, db={target}): {exc}"
        ) from exc


@app.command()
def scaffold(
    prj_id: str = typer.Argument(..., help="Project ID"),
    skip_db_setup: bool = typer.Option(
        False, "--skip-db-setup", help="跳过数据库创建和校验 (用于测试)"
    ),
    source_mode: str = typer.Option(
        "fdw",
        "--source-mode",
        help="Row 层引用源表方式: fdw (外联表, 默认) / sync (物理同步)",
    ),
    user: str = typer.Option(
        "", "--user", "-u", help="Database user (defaults to workspace platform user)"
    ),
    password: str = typer.Option(
        "", "--password", prompt=True, hide_input=True, help="Database password"
    ),
) -> None:
    """Generate dbt project scaffolding from approved requirement assessments.

    Uses the workspace-level platform configuration (set during `antline init`).

    Automatically generates:
    - sources.yml with tables referenced in field mappings
    - row/ models (one per source table, SELECT *)
    - map/ models (field mappings from assessment)
    - clean/ models (with type-casting TODOs from target schema)

    ``source_mode`` controls how row layer references source tables:

    * ``fdw`` (default) – Use PostgreSQL Foreign Data Wrapper. A setup
      script ``sql/fdw_setup.sql`` is generated. Run it in the target DB
      so row models can query source tables as foreign tables.

    * ``sync`` – Row layer expects data to have been physically synced into
      the target database ODS layer (schema ``ods_<source_id>``). Run the
      extract job before ``dbt build``.
    """
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    # Read workspace platform config
    platform = state.workspace_platform()
    if not platform:
        console.print(
            "[red]Workspace platform not configured. "
            "Run `antline init` with --db-type, --host, --port, --user, --password.[/]"
        )
        raise typer.Exit(1)

    db_type = platform.get("db_type", "postgresql")
    host = platform.get("host", "localhost")
    port = platform.get("port", 5432)
    # Use CLI args, fallback to workspace platform
    resolved_user = user or platform.get("user", "")
    resolved_password = password or platform.get("password", "")
    db_name = platform.get("database", "")
    target_db = db_name or prj_id.replace("-", "_").lower()
    target_db_type = db_type.lower()

    # Validate credentials before any DB operation
    if not skip_db_setup:
        if not resolved_user:
            console.print(
                "[red]Database user is required. "
                "Pass --user or configure it in the workspace (antline init --user).[/]"
            )
            raise typer.Exit(1)
        console.print(f"[dim]Validating credentials for {resolved_user}@{host}:{port} …[/]")
        try:
            _validate_db_credentials(
                db_type=target_db_type,
                host=host,
                port=port,
                user=resolved_user,
                password=resolved_password,
            )
        except ConnectionError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from None

    console.print(f"[bold]Scaffolding[/] dbt project for {prj_id} …")
    console.print(f"  Platform: {db_type} @ {host}:{port}/{target_db}")

    # Validate / create target database
    if not skip_db_setup:
        console.print(f"  Checking target database [dim]{target_db}[/] …")
        try:
            _ensure_target_database(
                db_type=target_db_type,
                host=host,
                port=port,
                user=resolved_user,
                password=resolved_password,
                db_name=target_db,
            )
            console.print(f"  [green]Database created:[/] {target_db}")
            if target_db_type == "postgresql":
                console.print("  [green]Schemas created:[/] row, map, clean")
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            raise typer.Exit(1) from None
        except Exception as e:
            console.print(f"[red]Database setup failed:[/] {e}")
            raise typer.Exit(1) from None
    else:
        console.print("  [dim]Skipping database setup (--skip-db-setup)[/]")

    # Collect data from assessments
    used_tables, req_mappings = _collect_scaffold_data(prj, state)

    if not used_tables:
        console.print(
            "[yellow]Warning:[/] No table mappings found in assessments. "
            "Scaffold will generate placeholder models."
        )

    # Per-project dbt directory under projects/
    dbt_dir = state.root / "projects" / prj_id / "dbt"

    # Generate all dbt files
    _generate_dbt_project_yml(prj, dbt_dir)
    _generate_dbt_profile(
        prj, dbt_dir, target_db_type, host, port, resolved_user, resolved_password, target_db
    )
    _generate_sources_yml(prj, state, dbt_dir, used_tables, source_mode=source_mode)
    _generate_row_models(prj, state, dbt_dir, used_tables)
    _generate_map_models(prj, state, dbt_dir, req_mappings)
    _generate_clean_models(prj, state, dbt_dir)

    fdw_script = None
    if source_mode == "fdw":
        fdw_script = _generate_fdw_script(prj, state, dbt_dir, used_tables, target_db_type)

    git_add_all(state.root)
    git_commit(f"feat(project): scaffold dbt for {prj_id}", state.root)

    # Report
    total_row = sum(len(t) for t in used_tables.values())
    total_map = sum(len(t) for t in req_mappings.values())

    console.print(f"[green]Scaffolded dbt project:[/] {dbt_dir}")
    console.print(f"  mode: [bold]{source_mode}[/]")
    console.print(f"  sources.yml: {len(used_tables)} source(s), {total_row} table(s)")
    console.print(f"  row/ models: {total_row}")
    console.print(f"  map/ models: {total_map}")
    console.print(f"  clean/ models: {total_map}")
    if fdw_script:
        console.print(f"  fdw script: {fdw_script}")
    console.print("\n  Next steps:")
    if source_mode == "fdw" and fdw_script:
        console.print(f"  1. Run FDW setup: psql -d {target_db} -f {fdw_script}")
        console.print("     (creates foreign tables so row layer can query source data)")
        console.print("  2. Review dbt/models/sources.yml — verify table references")
        console.print("  3. Review dbt/models/map/*.sql — adjust transform/missing mappings")
        console.print("  4. Implement dbt/models/clean/*.sql — add type casting & cleaning")
        console.print(f"  5. Run dbt build from: {dbt_dir}")
    elif source_mode == "sync":
        console.print("  1. Run extract job to sync source data into target DB ODS layer")
        console.print("     (row layer expects tables in ods_<source_id> schemas)")
        console.print("  2. Review dbt/models/sources.yml — verify table references")
        console.print("  3. Review dbt/models/map/*.sql — adjust transform/missing mappings")
        console.print("  4. Implement dbt/models/clean/*.sql — add type casting & cleaning")
        console.print(f"  5. Run dbt build from: {dbt_dir}")
    else:
        console.print("  1. Review dbt/models/sources.yml — verify table references")
        console.print("  2. Review dbt/models/map/*.sql — adjust transform/missing mappings")
        console.print("  3. Implement dbt/models/clean/*.sql — add type casting & cleaning")
        console.print(f"  4. Run dbt build from: {dbt_dir}")


@app.command()
def compile(
    prj_id: str = typer.Argument(..., help="Project ID"),
    model: str = typer.Option(
        "", "--model", "-m", help="指定模型名 (如 row_patients / map_patients)"
    ),
) -> None:
    """Compile dbt models to validate SQL syntax without executing.

    Examples:
        antline project compile PRJ-001           # 全局校验
        antline project compile PRJ-001 -m row_patients   # 校验单个模型
    """
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    dbt_dir = state.root / "projects" / prj_id / "dbt"
    if not (dbt_dir / "dbt_project.yml").exists():
        console.print(
            f"[red]No dbt project found. Run `antline project scaffold {prj_id}` first.[/]"
        )
        raise typer.Exit(1)

    import subprocess

    cmd = ["dbt", "compile"]
    if model:
        cmd.extend(["--select", model])

    console.print(f"[bold]Compiling[/] {prj_id} …")
    result = subprocess.run(
        cmd, cwd=dbt_dir, capture_output=False, env=_get_dbt_env(prj_id, state.workspace_platform())
    )

    if result.returncode == 0:
        console.print(f"[green]Compile successful:[/] {prj_id}")
    else:
        console.print(f"[red]Compile failed:[/] {prj_id}")
        raise typer.Exit(result.returncode)


@app.command()
def build(
    prj_id: str = typer.Argument(..., help="Project ID"),
    version: str = typer.Option("", "--version", "-v", help="Version tag (auto if omitted)"),
) -> None:
    """Build the project (call dbt run)."""
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    dbt_dir = state.root / "projects" / prj_id / "dbt"
    if not (dbt_dir / "dbt_project.yml").exists():
        console.print(
            f"[red]No dbt project found. Run `antline project scaffold {prj_id}` first.[/]"
        )
        raise typer.Exit(1)

    import subprocess

    console.print(f"[bold]Building[/] {prj_id} with dbt …")
    result = subprocess.run(
        ["dbt", "build"],
        cwd=dbt_dir,
        capture_output=False,
        env=_get_dbt_env(prj_id, state.workspace_platform()),
    )

    vid = version or f"v{len(prj.versions) + 1}.0.0"
    pv = ProjectVersion(
        id=vid,
        dbt_manifest=str(dbt_dir / "target" / "manifest.json"),
        passed=(result.returncode == 0),
    )
    prj.versions.append(pv)
    state.save_project(prj)

    git_add_all(state.root)
    git_commit(f"build(project): {prj_id} {vid}", state.root)

    if result.returncode == 0:
        console.print(f"[green]Build successful:[/] {vid}")
    else:
        console.print(f"[red]Build failed:[/] {vid}")
        raise typer.Exit(result.returncode)


@app.command()
def validate(
    prj_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Run data quality validation (dbt tests + custom checks)."""
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    dbt_dir = state.root / "projects" / prj_id / "dbt"
    if not (dbt_dir / "dbt_project.yml").exists():
        console.print("[red]No dbt project found. Run scaffold first.[/]")
        raise typer.Exit(1)

    import json
    import subprocess
    from datetime import datetime, timezone

    console.print(f"[bold]Running tests[/] for {prj_id} …")
    result = subprocess.run(
        ["dbt", "test"],
        cwd=dbt_dir,
        capture_output=False,
        env=_get_dbt_env(prj_id, state.workspace_platform()),
    )

    # Parse run_results.json for all test results
    run_results_path = dbt_dir / "target" / "run_results.json"
    passed_tests: list[dict] = []
    failed_tests: list[dict] = []
    if run_results_path.exists():
        try:
            run_results = json.loads(run_results_path.read_text())
            for r in run_results.get("results", []):
                info = {
                    "unique_id": r.get("unique_id", ""),
                    "status": r.get("status", ""),
                    "failures": r.get("failures", 0),
                    "message": r.get("message", ""),
                }
                if r.get("status") in ("fail", "error"):
                    failed_tests.append(info)
                elif r.get("status") == "pass":
                    passed_tests.append(info)
        except Exception:
            pass  # If parsing fails, fall back to basic report

    # Generate detailed QC report
    qc_dir = state.root / "projects" / prj_id / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    report_path = qc_dir / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    status = "PASSED" if result.returncode == 0 else "FAILED"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = len(passed_tests) + len(failed_tests)

    report_lines = [
        f"# QC Report: {prj_id}",
        "",
        f"**Status:** {status}",
        f"**Timestamp:** {timestamp}",
        f"**Summary:** {len(passed_tests)} passed / {len(failed_tests)} failed / {total} total",
        "",
    ]

    if failed_tests:
        report_lines.append(f"## Failed Tests ({len(failed_tests)})")
        report_lines.append("")
        for idx, t in enumerate(failed_tests, start=1):
            parts = t["unique_id"].split(".")
            test_name = parts[-2] if len(parts) >= 2 else t["unique_id"]
            report_lines.append(f"### {idx}. {test_name}")
            report_lines.append(f"- **Status:** {t['status']}")
            report_lines.append(f"- **Failure Count:** {t['failures']}")
            if t["message"]:
                report_lines.append(f"- **Details:** {t['message']}")
            report_lines.append("")

    if passed_tests:
        report_lines.append(f"## Passed Tests ({len(passed_tests)})")
        report_lines.append("")
        report_lines.append("| # | Test Name | Status |")
        report_lines.append("|---|-----------|--------|")
        for idx, t in enumerate(passed_tests, start=1):
            parts = t["unique_id"].split(".")
            test_name = parts[-2] if len(parts) >= 2 else t["unique_id"]
            report_lines.append(f"| {idx} | {test_name} | pass |")
        report_lines.append("")

    if not passed_tests and not failed_tests:
        if result.returncode == 0:
            report_lines.append("All tests passed successfully.")
        else:
            report_lines.append(
                "Tests failed, but no detailed results could be extracted from run_results.json."
            )
        report_lines.append("")

    report_path.write_text("\n".join(report_lines))

    if result.returncode == 0:
        prj.status = ProjectStatus.QC_PASSED
        state.save_project(prj)
        git_add_all(state.root)
        git_commit(f"qc(project): {prj_id} passed", state.root)
        console.print(f"[green]QC passed:[/] {prj_id}")
    else:
        console.print(f"[red]QC failed:[/] {prj_id}")
        console.print(f"  Report: {report_path}")
        raise typer.Exit(result.returncode)


@app.command()
def deliver(
    prj_id: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Deliver the project (mark as production-ready)."""
    state = ProjectState()
    prj = state.get_project(prj_id)
    if not prj:
        console.print(f"[red]Project not found:[/] {prj_id}")
        raise typer.Exit(1)

    if prj.status != ProjectStatus.QC_PASSED:
        console.print(
            f"[red]Project {prj_id} has not passed QC. Current status: {prj.status.value}[/]"
        )
        raise typer.Exit(1)

    prj.status = ProjectStatus.DELIVERED
    state.save_project(prj)
    git_add_all(state.root)
    git_commit(f"deliver(project): {prj_id}", state.root)

    console.print(f"[green]Delivered:[/] {prj_id}")
    console.print("  Status: delivered")
    console.print("  Next: Run dbt to promote preview models to prod target")
