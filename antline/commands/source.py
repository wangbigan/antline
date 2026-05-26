"""Data source management commands.

Passwords are never stored in source configuration files.
They must be provided at runtime for each database operation.
"""

from __future__ import annotations

import typer
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import text

from antline.core.audit import log_operation
from antline.core.config import ProjectState
from antline.core.db import explore_source, get_engine
from antline.core.extract import extract_source_to_ods
from antline.core.fdw import setup_fdw_for_source
from antline.core.git import git_add_all, git_commit
from antline.core.models import DataSource, DataSourceType, SourceExploreReport
from antline.core.report_md import render_explore_report

app = typer.Typer(no_args_is_help=True)
console = Console()


def _prompt_password() -> str:
    """Prompt for database password with hidden input."""
    return typer.prompt("Database password", hide_input=True)


@app.command()
def add(
    db_type: DataSourceType = typer.Option(..., "--type", "-t", help="Database type"),
    host: str = typer.Option("localhost", "--host", "-h", help="Host address"),
    port: int = typer.Option(0, "--port", "-P", help="Port (0 = auto by type)"),
    database: str = typer.Option(..., "--database", "-d", help="Database name"),
    user: str = typer.Option(..., "--user", "-u", help="Username"),
    password: str = typer.Option("", "--password", help="Password (prompted if not provided)"),
    name: str = typer.Option("", "--name", "-n", help="Display name (defaults to database)"),
    source_id: str = typer.Option("", "--id", help="Custom ID (auto-generated if omitted)"),
    test_connection: bool = typer.Option(
        True, "--test-connection/--no-test-connection", help="Test connection before saving"
    ),
) -> None:
    """Add a new data source.

    Password is prompted at runtime and never stored.
    """
    state = ProjectState()

    # Auto port
    if port == 0:
        port = {
            DataSourceType.POSTGRESQL: 5432,
            DataSourceType.MYSQL: 3306,
            DataSourceType.TIDB: 4000,
        }[db_type]

    sid = source_id or state.next_source_id()
    source = DataSource(
        id=sid,
        name=name or database,
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        user=user,
    )

    if test_connection:
        if not password:
            password = _prompt_password()
        console.print(f"[dim]Connecting {host}:{port}/{database} …[/]", end=" ")
        try:
            engine = get_engine(source, password)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            console.print("[green]ok[/]")
        except Exception as exc:
            error_msg = str(exc).lower()
            if "does not exist" in error_msg or ("database" in error_msg and "exist" in error_msg):
                console.print(
                    f"[red]failed[/]\n  Database '{database}' does not exist on {host}:{port}"
                )
            elif "authentication" in error_msg or "password" in error_msg:
                console.print("[red]failed[/]\n  Authentication failed — check user/password")
            elif "connection" in error_msg or "refused" in error_msg or "timeout" in error_msg:
                console.print(
                    f"[red]failed[/]\n  Cannot connect to {host}:{port} — check host/port"
                )
            else:
                console.print(f"[red]failed[/]\n  {exc}")
            raise typer.Exit(1) from None

        log_operation(
            state.root, "source_add_test", user, f"{host}:{port}/{database}", {"source_id": sid}
        )

    state.save_source(source)
    git_add_all(state.root)
    git_commit(f"feat(source): add {sid} ({db_type.value})", state.root)

    console.print(f"[green]Added source:[/] {sid} — {source.name} ({db_type.value})")
    console.print(f"  Connection: {host}:{port}/{database}")


@app.command("list")
def cmd_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all configured data sources."""
    state = ProjectState()
    sources = state.list_sources()

    if json_output:
        console.print(
            yaml.safe_dump(
                [s.model_dump(mode="json") for s in sources],
                allow_unicode=True,
            )
        )
        return

    if not sources:
        console.print("[dim]No sources configured. Run `antline source add`.[/dim]")
        return

    table = Table("ID", "Name", "Type", "Host", "Port", "Database")
    for s in sources:
        table.add_row(s.id, s.name, s.db_type.value, s.host, str(s.port), s.database)
    console.print(table)


@app.command()
def explore(
    source_id: str = typer.Argument(..., help="Source ID to explore"),
    max_tables: int = typer.Option(
        0, "--max-tables", "-m", help="Limit number of tables to explore (0 = no limit)"
    ),
    no_mask: bool = typer.Option(
        False, "--no-mask", help="Disable sample data masking for sensitive fields"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON/YAML"),
) -> None:
    """Explore a data source and generate metadata report.

    Password is prompted at runtime and never stored.
    """
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    password = _prompt_password()

    console.print(f"[bold]Exploring[/] {source_id} ({source.name}) …")
    report = explore_source(
        source, password=password, max_tables=max_tables, mask_sensitive=not no_mask
    )

    log_operation(
        state.root,
        "source_explore",
        source.user,
        f"{source.host}:{source.port}/{source.database}",
        {"source_id": source_id, "tables": len(report.tables)},
    )

    explore_dir = state.root / "sources" / source_id / "explore"
    explore_dir.mkdir(parents=True, exist_ok=True)

    # Save structured YAML for agents
    yml_path = explore_dir / "report.yml"
    with open(yml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(report.model_dump(mode="json"), f, sort_keys=False, allow_unicode=True)

    # Save Markdown for human reading
    md_path = explore_dir / "report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_explore_report(report))

    # Git commit reports
    git_add_all(state.root)
    git_commit(f"docs: explore report for {source_id}", state.root)

    if json_output:
        console.print(yaml.safe_dump(report.model_dump(mode="json"), allow_unicode=True))
    else:
        _print_explore_summary(report)

    console.print("\n[green]Reports saved:[/]")
    console.print(f"  YAML (agent): {yml_path}")
    console.print(f"  Markdown (human): {md_path}")


def _print_explore_summary(report: SourceExploreReport) -> None:
    summary = report.summary
    console.print(f"\n[bold]Database:[/] {summary['database']} ({summary['db_type']})")
    console.print(
        f"Tables: {summary['total_tables']} | Rows: {summary['total_rows']:,} | Columns: {summary['total_columns']}"
    )

    table = Table("Table", "Schema", "Rows", "Columns", "PK")
    for t in report.tables[:20]:  # cap at 20 for terminal sanity
        table.add_row(
            t.name,
            t.schema_name or "-",
            f"{t.row_count:,}" if t.row_count >= 0 else "?",
            str(len(t.columns)),
            ", ".join(t.primary_key) or "-",
        )
    console.print(table)

    if len(report.tables) > 20:
        console.print(f"[dim]… and {len(report.tables) - 20} more tables[/dim]")


@app.command("show")
def show_source(
    source_id: str = typer.Argument(..., help="Source ID"),
) -> None:
    """Show details of a data source."""
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    console.print(f"[bold]{source.id}[/]")
    console.print(f"  Name:     {source.name}")
    console.print(f"  Type:     {source.db_type.value}")
    console.print(f"  Host:     {source.host}:{source.port}")
    console.print(f"  Database: {source.database}")
    console.print(f"  User:     {source.user}")


@app.command()
def setup(
    source_id: str = typer.Argument(..., help="Source ID to set up"),
    mode: str = typer.Option(
        "fdw", "--mode", "-m", help="接入模式: fdw (外联表) / sync (物理同步)"
    ),
    tables: str = typer.Option(
        "", "--tables", "-t", help="指定表名,逗号分隔 (默认: 全部表)"
    ),
    target_db: str = typer.Option(
        "", "--target-db", "-D", help="本地目标数据库名 (默认从 workspace 配置读取)"
    ),
    target_user: str = typer.Option(
        "", "--target-user", "-u", help="本地目标数据库用户 (默认从 workspace 配置读取)"
    ),
    target_password: str = typer.Option(
        "", "--target-password", help="本地目标数据库密码"
    ),
    source_password: str = typer.Option(
        "", "--source-password", help="源数据库密码 (默认提示输入)"
    ),
    batch_size: int = typer.Option(
        10000, "--batch-size", "-b", help="同步批次大小 (仅 sync 模式)"
    ),
) -> None:
    """在本地目标数据库中接入源数据: FDW 外联表或物理同步.

    FDW 模式 (默认): 在本地 PostgreSQL 中创建外部表,可直接查询远程数据.
    Sync 模式: 将远程表数据抽取到本地 ODS 层.

    示例:
        antline source setup SRC-001 --mode fdw
        antline source setup SRC-001 --mode sync --tables patients,admissions
        antline source setup SRC-001 --mode sync --target-db mydb --target-user postgres
    """
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    # Read workspace platform config
    platform = state.workspace_platform()
    if not platform:
        console.print(
            "[red]Workspace platform not configured. Run `antline init` first.[/]"
        )
        raise typer.Exit(1)

    db_type = platform.get("db_type", "postgresql")
    host = platform.get("host", "localhost")
    port = platform.get("port", 5432)
    platform_user = platform.get("user", "")

    if db_type != "postgresql":
        console.print(
            f"[red]FDW/Sync setup requires PostgreSQL target platform, got {db_type}[/]"
        )
        raise typer.Exit(1)

    # Resolve target database
    resolved_target_db = target_db or platform.get("database", "")
    if not resolved_target_db:
        console.print(
            "[red]Target database not specified. Use --target-db or set database in antline.yml platform config.[/]"
        )
        raise typer.Exit(1)

    # Resolve target user
    resolved_target_user = target_user or platform_user
    if not resolved_target_user:
        resolved_target_user = typer.prompt("Target database user")

    # Prompt for passwords
    if not target_password:
        target_password = typer.prompt("Target database password", hide_input=True)
    if not source_password:
        source_password = typer.prompt(
            f"Source database password for {source_id}", hide_input=True
        )

    # Validate target connection
    console.print(
        f"[dim]Checking target connection ({resolved_target_user}@{host}:{port}/{resolved_target_db}) …[/]",
        end=" ",
    )
    from sqlalchemy import create_engine, text

    target_conn = (
        f"postgresql+psycopg2://{resolved_target_user}:{target_password}"
        f"@{host}:{port}/{resolved_target_db}"
    )
    try:
        target_engine = create_engine(
            target_conn,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10, "gssencmode": "disable"},
        )
        with target_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        console.print("[green]ok[/]")
    except Exception as exc:
        console.print(f"[red]failed[/]\n  {exc}")
        raise typer.Exit(1) from None

    # Parse table list
    table_list = [t.strip() for t in tables.split(",") if t.strip()] or None

    if mode == "fdw":
        console.print(
            f"[bold]Setting up FDW[/] for {source_id} → {resolved_target_db} …"
        )
        result = setup_fdw_for_source(
            source=source,
            source_password=source_password,
            target_host=host,
            target_port=port,
            target_database=resolved_target_db,
            target_user=resolved_target_user,
            target_password=target_password,
            tables=table_list,
        )

        if result["success"]:
            console.print(f"[green]{result['message']}[/]")
            console.print(f"  Schema: [bold]{result['schema']}[/]")
            if result["tables"]:
                console.print(f"  Tables: {', '.join(result['tables'][:10])}")
                if len(result["tables"]) > 10:
                    console.print(f"    … and {len(result['tables']) - 10} more")
        else:
            console.print(f"[red]FDW setup failed:[/] {result['message']}")
            raise typer.Exit(1)

        log_operation(
            state.root,
            "source_setup_fdw",
            resolved_target_user,
            f"{host}:{port}/{resolved_target_db}",
            {
                "source_id": source_id,
                "schema": result["schema"],
                "tables": result["tables"],
            },
        )

    elif mode == "sync":
        # Determine source schema
        if source.db_type.value in ("mysql", "tidb"):
            source_schema = source.database
        else:
            # For PostgreSQL, try to infer from explore report
            reports = _load_explore_reports(state, [source_id])
            if source_id in reports and reports[source_id].tables:
                source_schema = reports[source_id].tables[0].schema_name or None
            else:
                source_schema = None

        # If no specific tables requested, load from explore report
        if not table_list:
            reports = _load_explore_reports(state, [source_id])
            if source_id in reports:
                table_list = [t.name for t in reports[source_id].tables]
            else:
                console.print(
                    "[yellow]Warning:[/] No tables specified and no explore report found. "
                    "Run `antline source explore` first or use --tables."
                )
                raise typer.Exit(0)

        ods_schema = f"ods_{source_id.lower()}"
        console.print(
            f"[bold]Syncing[/] {source_id} → {resolved_target_db}.{ods_schema} …"
        )
        console.print(f"  Tables: {len(table_list)}")

        job_result = extract_source_to_ods(
            source=source,
            source_password=source_password,
            target_engine=target_engine,
            tables=table_list,
            ods_schema=ods_schema,
            source_schema=source_schema,
            batch_size=batch_size,
        )

        for r in job_result.results:
            status_icon = "[green]✓[/]" if r.status == "success" else "[red]✗[/]"
            if r.status == "skipped":
                status_icon = "[yellow]⊘[/]"
            msg = f"    {r.table}: "
            if r.status == "success":
                msg += f"{r.rows_copied:,} rows {status_icon}"
            elif r.status == "failed":
                msg += f"failed {status_icon} — {r.message}"
            else:
                msg += f"skipped {status_icon} — {r.message}"
            console.print(msg)

        log_operation(
            state.root,
            "source_setup_sync",
            resolved_target_user,
            f"{host}:{port}/{resolved_target_db}",
            {
                "source_id": source_id,
                "ods_schema": ods_schema,
                "total_rows": job_result.total_rows,
                "success": job_result.success_count,
                "failed": job_result.failed_count,
            },
        )

        if job_result.failed_count > 0:
            console.print(
                f"[yellow]Sync complete with issues:[/] "
                f"{job_result.total_rows:,} rows, "
                f"{job_result.success_count} success, {job_result.failed_count} failed"
            )
        else:
            console.print(
                f"[green]Sync complete:[/] {job_result.total_rows:,} rows, "
                f"{job_result.success_count} table(s)"
            )
    else:
        console.print(f"[red]Unknown mode:[/] {mode}. Use 'fdw' or 'sync'.")
        raise typer.Exit(1)

    git_add_all(state.root)
    git_commit(f"feat(source): setup {source_id} ({mode})", state.root)


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


@app.command()
def update(
    source_id: str = typer.Argument(..., help="Source ID to update"),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    host: str | None = typer.Option(None, "--host", "-h", help="Host address"),
    port: int | None = typer.Option(None, "--port", "-P", help="Port"),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    user: str | None = typer.Option(None, "--user", "-u", help="Username"),
    db_type: DataSourceType | None = typer.Option(None, "--type", "-t", help="Database type"),
) -> None:
    """Update an existing data source. Only specified fields are changed.

    Passwords cannot be updated here — they are never stored.
    """
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    updated = source.model_copy()
    if name is not None:
        updated.name = name
    if host is not None:
        updated.host = host
    if port is not None:
        updated.port = port
    if database is not None:
        updated.database = database
    if user is not None:
        updated.user = user
    if db_type is not None:
        updated.db_type = db_type

    state.save_source(updated)
    git_add_all(state.root)
    git_commit(f"feat(source): update {source_id}", state.root)

    console.print(f"[green]Updated source:[/] {source_id}")
    console.print(f"  Connection: {updated.host}:{updated.port}/{updated.database}")


@app.command()
def remove(
    source_id: str = typer.Argument(..., help="Source ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a data source."""
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(
            f"Remove source {source_id} ({source.name} — {source.host}:{source.port}/{source.database})?"
        )
        if not confirm:
            console.print("[yellow]Cancelled.[/]")
            raise typer.Exit(0)

    state.delete_source(source_id)
    git_add_all(state.root)
    git_commit(f"feat(source): remove {source_id}", state.root)
    console.print(f"[green]Removed source:[/] {source_id}")
