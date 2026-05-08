"""Typer CLI entry point for Antline."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from antline.commands import project, requirement, schema, source
from antline.core.config import CONFIG_FILE, ProjectState
from antline.core.git import ensure_gitignore, git_add_all, git_commit, git_init, is_git_repo
from antline.core.models import DataSourceType

app = typer.Typer(
    name="antline",
    help="CLI data production management tool",
    no_args_is_help=True,
    add_completion=True,
)


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"antline {version('antline')}")
        raise typer.Exit()


@app.callback()
def _callback(
    version: bool = typer.Option(
        False, "--version", "-v", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Antline CLI."""
    pass

app.add_typer(source.app, name="source", help="Data source management")
app.add_typer(requirement.app, name="requirement", help="Data requirement management")
app.add_typer(project.app, name="project", help="Data project management")
app.add_typer(schema.app, name="schema", help="Target schema management")

console = Console()

DEFAULT_CONFIG = {
    "project": {
        "name": "",
        "description": "",
    },
    "platform": {
        "db_type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "user": "",
        "password": "",
        "database": "",
    },
    "paths": {
        "sources": "sources",
        "requirements": "requirements",
        "projects": "projects",
        "reports": "reports",
    },
}


@app.command()
def init(
    path: Path = typer.Option(".", "--path", "-p", help="Directory to initialize the project in"),
    name: str = typer.Option("", "--name", "-n", help="Project name"),
    db_type: DataSourceType = typer.Option(
        DataSourceType.POSTGRESQL, "--db-type", "-t", help="Target database type"
    ),
    host: str = typer.Option("localhost", "--host", "-h", help="Database host"),
    port: int = typer.Option(5432, "--port", "-P", help="Database port"),
    user: str = typer.Option("", "--user", "-u", help="Database user"),
    password: str = typer.Option("", "--password", prompt=True, hide_input=True, help="Database password"),
    database: str = typer.Option("", "--database", "-d", help="Default database name"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
) -> None:
    """Initialize a new Antline workspace with platform configuration."""
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)

    config_path = root / CONFIG_FILE
    if config_path.exists() and not force:
        console.print(f"[yellow]Already initialized:[/] {config_path}")
        raise typer.Exit(0)

    config = DEFAULT_CONFIG.copy()
    config["project"]["name"] = name or root.name
    config["platform"] = {
        "db_type": db_type.value,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database or root.name.replace("-", "_").lower(),
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, allow_unicode=True)

    # Ensure state dirs
    ProjectState(root)
    # touch .gitkeep files
    for d in ("sources", "requirements", "projects", "reports"):
        (root / d / ".gitkeep").touch(exist_ok=True)

    # Git setup
    if not is_git_repo(root):
        if git_init(root):
            ensure_gitignore(root)
            console.print(f"[green]Initialized git repo:[/] {root / '.git'}")
        else:
            console.print("[yellow]Warning: git init failed (sandbox restrictions?)[/]")

    git_add_all(root)
    git_commit("chore: initialize antline project", root)

    console.print(f"[green]Initialized Antline workspace:[/] {root}")
    console.print(f"  Config: {config_path}")
    console.print(f"  Platform: {db_type.value} @ {host}:{port}/{database or root.name}")
    console.print(f"  Run `cd {root.name}` to start working.")


@app.command()
def status() -> None:
    """Show project status overview."""
    state = ProjectState()
    config_path = state.root / CONFIG_FILE
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}

    sources = state.list_sources()
    reqs = state.list_requirements()
    prjs = state.list_projects()

    console.print(f"[bold]Project:[/] {config.get('project', {}).get('name', 'N/A')}")
    console.print(f"  Root: {state.root}")
    console.print()

    table = Table("Entity", "Count", "Status")
    table.add_row(
        "Sources", str(len(sources)), ", ".join({s.db_type.value for s in sources}) or "-"
    )
    table.add_row("Requirements", str(len(reqs)), ", ".join({r.status.value for r in reqs}) or "-")
    table.add_row("Projects", str(len(prjs)), ", ".join({p.status.value for p in prjs}) or "-")
    console.print(table)


def main() -> None:
    app()
