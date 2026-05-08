"""Data source management commands."""

from __future__ import annotations

import typer
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import text

from antline.core.config import ProjectState
from antline.core.db import explore_source, get_engine
from antline.core.git import git_add_all, git_commit
from antline.core.models import DataSource, DataSourceType, SourceExploreReport
from antline.core.report_md import render_explore_report

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def add(
    db_type: DataSourceType = typer.Option(..., "--type", "-t", help="Database type"),
    host: str = typer.Option("localhost", "--host", "-h", help="Host address"),
    port: int = typer.Option(0, "--port", "-P", help="Port (0 = auto by type)"),
    database: str = typer.Option(..., "--database", "-d", help="Database name"),
    user: str = typer.Option(..., "--user", "-u", help="Username"),
    password: str = typer.Option(
        ..., "--password", "-p", prompt=True, hide_input=True, help="Password"
    ),
    name: str = typer.Option("", "--name", "-n", help="Display name (defaults to database)"),
    source_id: str = typer.Option("", "--id", help="Custom ID (auto-generated if omitted)"),
    no_test: bool = typer.Option(False, "--no-test-connection", help="Skip connection validation"),
) -> None:
    """Add a new data source."""
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
        password=password,
    )

    if not no_test:
        console.print(f"[dim]Connecting {host}:{port}/{database} …[/]", end=" ")
        try:
            engine = get_engine(source)
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
            raise typer.Exit(1)

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
    """Explore a data source and generate metadata report."""
    state = ProjectState()
    source = state.get_source(source_id)
    if not source:
        console.print(f"[red]Source not found:[/] {source_id}")
        raise typer.Exit(1)

    console.print(f"[bold]Exploring[/] {source_id} ({source.name}) …")
    report = explore_source(source, max_tables=max_tables, mask_sensitive=not no_mask)

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
def update(
    source_id: str = typer.Argument(..., help="Source ID to update"),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    host: str | None = typer.Option(None, "--host", "-h", help="Host address"),
    port: int | None = typer.Option(None, "--port", "-P", help="Port"),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    user: str | None = typer.Option(None, "--user", "-u", help="Username"),
    password: str | None = typer.Option(
        None, "--password", "-p", hide_input=True, help="Password (omit to keep current)"
    ),
    db_type: DataSourceType | None = typer.Option(None, "--type", "-t", help="Database type"),
) -> None:
    """Update an existing data source. Only specified fields are changed."""
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
    if password is not None and password != "":
        updated.password = password
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
