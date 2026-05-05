"""Schema import and management commands."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from rich.console import Console

from antline.core.config import ProjectState
from antline.core.csv_schema import import_schema_from_csv, save_schemas_as_yaml
from antline.core.git import git_add_all, git_commit

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("import")
def import_csv(
    csv_file: Path = typer.Argument(..., help="Path to CSV file with schema definitions"),
    output_dir: Path = typer.Option(
        "target_schema",
        "--output-dir",
        "-o",
        help="Directory to save generated YAML files",
    ),
) -> None:
    """Import target schema definitions from a CSV file.

    Expected CSV columns:
        module, table_name, table_comment, field_name, field_type, field_comment, example

    Example:
        antline schema import /path/to/schema.csv --output-dir target_schema
    """
    if not csv_file.exists():
        console.print(f"[red]File not found:[/] {csv_file}")
        raise typer.Exit(1)

    console.print(f"[bold]Importing[/] schema from {csv_file} …")

    schemas = import_schema_from_csv(csv_file)

    # Resolve output dir relative to project root if inside one
    try:
        state = ProjectState()
        output_dir = state.root / output_dir
    except RuntimeError:
        pass  # not inside a project, use cwd-relative

    paths = save_schemas_as_yaml(schemas, output_dir)

    for p in paths:
        console.print(f"  [green]Created[/] {p}")
    console.print(f"\n[green]Imported {len(schemas)} table(s).[/]")

    # Git commit if inside project
    try:
        state = ProjectState()
        git_add_all(state.root)
        git_commit(f"feat(schema): import {len(schemas)} tables from {csv_file.name}", state.root)
    except RuntimeError:
        pass


@app.command("show")
def show_schema(
    table_name: str = typer.Argument(..., help="Table name to show"),
    schema_dir: Path = typer.Option(
        "target_schema", "--dir", "-d", help="Directory containing schema YAML files"
    ),
) -> None:
    """Show a target schema definition."""
    try:
        state = ProjectState()
        schema_dir = state.root / schema_dir
    except RuntimeError:
        pass

    path = schema_dir / f"{table_name}.yaml"
    if not path.exists():
        console.print(f"[red]Schema not found:[/] {path}")
        raise typer.Exit(1)

    data = yaml.safe_load(path.read_text())
    console.print(f"[bold]{data['table']}[/]")
    if data.get("description"):
        console.print(f"  {data['description']}")

    console.print(f"\n  Fields ({len(data['fields'])}):")
    for f in data["fields"]:
        nullable = "NULL" if f.get("nullable", True) else "NOT NULL"
        console.print(f"    - {f['name']}: {f['data_type']} {nullable}")
        if f.get("description"):
            console.print(f"      {f['description']}")


@app.command("list")
def list_schemas(
    schema_dir: Path = typer.Option(
        "target_schema", "--dir", "-d", help="Directory containing schema YAML files"
    ),
) -> None:
    """List all target schema definitions."""
    try:
        state = ProjectState()
        schema_dir = state.root / schema_dir
    except RuntimeError:
        pass

    if not schema_dir.exists():
        console.print(f"[dim]No schema directory found: {schema_dir}[/dim]")
        return

    files = sorted(schema_dir.glob("*.yaml"))
    if not files:
        console.print(f"[dim]No schema files in {schema_dir}[/dim]")
        return

    for f in files:
        data = yaml.safe_load(f.read_text())
        console.print(f"  {data['table']} — {len(data.get('fields', []))} fields")
