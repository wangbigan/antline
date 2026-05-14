"""Data requirement management commands."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from antline.core.config import ProjectState
from antline.core.csv_schema import import_schema_from_csv, save_schemas_as_yaml
from antline.core.git import git_add_all, git_commit
from antline.core.models import (
    AssessmentRisk,
    FieldMapping,
    Requirement,
    RequirementAssessment,
    RequirementStatus,
    SourceExploreReport,
    TargetSchema,
)

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Requirement name"),
    background: str = typer.Option(
        "", "--background", "-b", help="Business context / why this requirement exists"
    ),
    goal: str = typer.Option("", "--goal", "-g", help="Target outcome / what to achieve"),
    target_schema: list[Path] = typer.Option(
        [], "--target-schema", "-s", help="Path to target schema YAML file(s) or directory"
    ),
    req_id: str = typer.Option("", "--id", help="Custom requirement ID"),
) -> None:
    """Create a new data requirement."""
    state = ProjectState()

    # Resolve all schema paths: files directly, directories recursively
    schema_paths: list[Path] = []
    for path in target_schema:
        if path.is_dir():
            schema_paths.extend(sorted(path.glob("*.yaml")))
            schema_paths.extend(sorted(path.glob("*.yml")))
        elif path.exists():
            schema_paths.append(path)
        else:
            console.print(f"[red]Path not found:[/] {path}")
            raise typer.Exit(1)

    schemas: list[TargetSchema] = []
    for sp in schema_paths:
        data = yaml.safe_load(sp.read_text())
        schemas.append(TargetSchema.model_validate(data))

    rid = req_id or state.next_requirement_id()
    req = Requirement(
        id=rid,
        name=name,
        background=background,
        goal=goal,
        target_schemas=schemas,
    )
    state.save_requirement(req)
    git_add_all(state.root)
    git_commit(f"feat(requirement): create {rid}", state.root)

    console.print(f"[green]Created requirement:[/] {rid} — {name}")
    if schemas:
        console.print(f"  Tables: {', '.join(s.table for s in schemas)}")


@app.command("list")
def cmd_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all requirements."""
    state = ProjectState()
    reqs = state.list_requirements()

    if json_output:
        console.print(yaml.safe_dump([r.model_dump(mode="json") for r in reqs], allow_unicode=True))
        return

    if not reqs:
        console.print("[dim]No requirements yet. Run `antline requirement create`.[/dim]")
        return

    table = Table("ID", "Name", "Status", "Assessed")
    for r in reqs:
        assessed = "✓" if r.assessment and r.assessment.assessed_at else "-"
        table.add_row(r.id, r.name, r.status.value, assessed)
    console.print(table)


@app.command()
def show(
    req_id: str = typer.Argument(..., help="Requirement ID"),
) -> None:
    """Show requirement details."""
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    console.print(f"[bold]{req.id}[/]  [dim]{req.status.value}[/]")
    console.print(f"  Name: {req.name}")
    if req.background:
        console.print(f"  Background: {req.background}")
    if req.goal:
        console.print(f"  Goal: {req.goal}")

    for schema in req.target_schemas:
        console.print(f"\n  Target Table: {schema.table}")
        for f in schema.fields:
            console.print(f"    - {f.name}: {f.data_type}{' NULL' if f.nullable else ' NOT NULL'}")
            if f.description:
                console.print(f"      {f.description}")

    if req.assessment:
        a = req.assessment
        console.print("\n  Assessment:")
        console.print(f"    Feasible: {'[green]YES[/]' if a.feasible else '[red]NO[/]'}")
        if a.risks:
            for risk in a.risks:
                color = {"low": "green", "medium": "yellow", "high": "red", "critical": "red"}
                console.print(
                    f"    [{color.get(risk.level, 'white')}]Risk ({risk.level}): {risk.description}[/]"
                )


@app.command()
def assess(
    req_id: str = typer.Argument(..., help="Requirement ID"),
    source_ids: list[str] = typer.Argument(..., help="One or more source IDs to assess against"),
    focus: str = typer.Option(
        "",
        "--focus",
        "-f",
        help="Comma-separated list of source table names to focus on (e.g. patients,admissions)",
    ),
    full_stats: bool = typer.Option(
        False,
        "--full",
        help="Include full field statistics (null rates, unique counts, top values). "
        "Without --focus, applies to all tables.",
    ),
) -> None:
    """Generate assessment materials (prompt + guide + template) for human/LLM review.

    This command does NOT auto-generate field mappings. Instead it produces:
    - prompt.md: an LLM prompt with target schemas and source metadata
    - guide.md: a human-readable guide for manual assessment
    - template.yml: an empty assessment template to be filled

    After filling out the assessment, save it as assessment.yml and run:
        antline requirement approve REQ-001
    """
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    if not req.target_schemas:
        console.print("[red]Requirement has no target schema. Define one first.[/]")
        raise typer.Exit(1)

    # Validate sources
    for sid in source_ids:
        if not state.get_source(sid):
            console.print(f"[red]Source not found:[/] {sid}")
            raise typer.Exit(1)

    focus_tables = [t.strip() for t in focus.split(",") if t.strip()] or None

    console.print(f"[bold]正在生成评估材料[/] {req_id} …")

    # Load explore reports
    from antline.core.assessment_prompt import (
        generate_assessment_template,
        generate_human_guide,
        generate_llm_prompt,
    )

    reports: list[SourceExploreReport] = []
    for sid in source_ids:
        report_path = state.root / "sources" / sid / "explore" / "report.yml"
        if report_path.exists():
            data = yaml.safe_load(report_path.read_text())
            if data:
                reports.append(SourceExploreReport.model_validate(data))

    if not reports:
        console.print(
            "[yellow]Warning:[/] No explore reports found. Run `antline source explore` first."
        )

    # Generate output files
    assessment_dir = state.root / "requirements" / req_id / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = assessment_dir / "prompt.md"
    guide_path = assessment_dir / "guide.md"
    template_path = assessment_dir / "template.md"

    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(generate_llm_prompt(req, reports, focus_tables, full_stats))

    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(generate_human_guide(req, reports, focus_tables, full_stats))

    with open(template_path, "w", encoding="utf-8") as f:
        f.write(generate_assessment_template(req, source_ids))

    # Update requirement status
    req.status = RequirementStatus.ASSESSED
    state.save_requirement(req)

    git_add_all(state.root)
    git_commit(f"feat(requirement): assess {req_id} (prompts generated)", state.root)

    console.print("\n[green]评估材料已生成:[/]")
    console.print(f"  LLM 提示词:  {prompt_path}")
    console.print(f"  人工指南:    {guide_path}")
    console.print(f"  评估模板:    {template_path}")
    console.print("\n[yellow]下一步操作:[/]")
    console.print("  1. 将 prompt.md 的内容复制给大模型，获取评估结果")
    console.print("  2. 人工审核修改后，保存为 assessment.md")
    console.print(f"  3. 审批通过: [bold]antline requirement approve {req_id}[/]")


@app.command()
def approve(
    req_id: str = typer.Argument(..., help="Requirement ID to approve"),
    assessment_file: Path = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to the completed assessment Markdown file. "
        "Defaults to reports/assessment/{req_id}_assessment.md",
    ),
    force: bool = typer.Option(False, "--force", help="Approve even if not feasible or already in project"),
    note: str = typer.Option(
        "", "--note", help="Re-approval reason (required when re-approving an IN_PROJECT requirement)"
    ),
) -> None:
    """Approve a requirement after manual review of the assessment.

    Reads the completed assessment Markdown file (with YAML frontmatter),
    validates it, and stores it in the requirement. Only approved requirements
    can be used to create projects.
    """
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    if req.status == RequirementStatus.IN_PROJECT:
        if not force:
            console.print(
                f"[red]Requirement {req_id} is already in a project. "
                "Use --force to re-approve with a note.[/]"
            )
            raise typer.Exit(1)
        if not note:
            console.print(
                "[red]Re-approving an IN_PROJECT requirement requires a --note "
                "explaining the reason.[/]"
            )
            raise typer.Exit(1)

    if req.status == RequirementStatus.APPROVED and not force:
        console.print(f"[yellow]已经审批通过:[/] {req_id} (使用 --force 强制更新)")
        raise typer.Exit(1)

    if req.status not in (RequirementStatus.ASSESSED, RequirementStatus.APPROVED, RequirementStatus.IN_PROJECT):
        console.print(f"[red]需求尚未评估。当前状态: {req.status.value}[/]")
        raise typer.Exit(1)

    # Determine assessment file path (default to .md)
    if assessment_file is None:
        assessment_file = state.root / "requirements" / req_id / "assessment" / "assessment.md"

    if not assessment_file.exists():
        console.print(
            f"[red]评估文件未找到:[/] {assessment_file}\n"
            f"请先运行 `antline requirement assess {req_id} <sources>`，"
            f"然后让大模型生成评估报告并保存为 {assessment_file.name}。"
        )
        raise typer.Exit(1)

    # Parse assessment from Markdown with YAML frontmatter
    content = assessment_file.read_text()
    frontmatter_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        console.print(
            "[red]评估文件格式错误：未找到 YAML frontmatter。[/]\n"
            "评估文件应以 `---` 开头，包含 YAML 结构化数据，"
            "然后再次以 `---` 结束，接着是 Markdown 正文。"
        )
        raise typer.Exit(1)

    try:
        data = yaml.safe_load(frontmatter_match.group(1))
    except Exception as exc:
        console.print(f"[red]解析 frontmatter 失败:[/] {exc}")
        raise typer.Exit(1)

    if not data:
        console.print("[red]评估文件 frontmatter 为空。[/]")
        raise typer.Exit(1)

    # Validate required fields
    if "field_mappings" not in data or not data["field_mappings"]:
        console.print("[red]评估缺少字段映射。请先完成模板填写。[/]")
        raise typer.Exit(1)

    # Build RequirementAssessment
    try:
        mappings = [FieldMapping.model_validate(m) for m in data.get("field_mappings", [])]
        risks = [AssessmentRisk.model_validate(r) for r in data.get("risks", [])]
        feasible = bool(data.get("feasible", False))

        try:
            rel_path = str(assessment_file.resolve().relative_to(state.root.resolve()))
        except ValueError:
            rel_path = str(assessment_file)

        assessment = RequirementAssessment(
            feasible=feasible,
            report_path=rel_path,
            source_ids=data.get("source_ids", []),
            field_mappings=mappings,
            risks=risks,
            notes=data.get("notes", ""),
            reapproval_reason=note,
            assessed_at=datetime.now(timezone(timedelta(hours=8))),
        )
    except Exception as exc:
        console.print(f"[red]评估数据无效:[/] {exc}")
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # Validate table/field references against explore reports
    # ------------------------------------------------------------------
    validation_errors: list[str] = []

    # Load all explore reports referenced by source_ids
    explore_reports: dict[str, SourceExploreReport] = {}
    for sid in data.get("source_ids", []):
        report_path = state.root / "sources" / sid / "explore" / "report.yml"
        if report_path.exists():
            report_data = yaml.safe_load(report_path.read_text())
            if report_data:
                explore_reports[sid] = SourceExploreReport.model_validate(report_data)

    for idx, m in enumerate(data.get("field_mappings", []), start=1):
        if m.get("mapping_type") == "missing":
            continue

        source_table = m.get("source_table") or ""
        source_field = m.get("source_field") or ""
        target_field = m.get("target_field", "")

        if not source_table or not source_field:
            continue

        # Extract pure table name (e.g. "src_20260508_001.patients" -> "patients")
        table_name = source_table.split(".")[-1]

        # Determine which source this table belongs to
        matched = False
        for sid, report in explore_reports.items():
            table_names = {t.name for t in report.tables}
            if table_name in table_names:
                matched = True
                # Find the table and check if the field exists
                for table in report.tables:
                    if table.name == table_name:
                        field_names = {c.name for c in table.columns}
                        if source_field not in field_names:
                            validation_errors.append(
                                f"  [{idx}] target={target_field}: "
                                f"field '{source_field}' not found in table '{table_name}' (source={sid})"
                            )
                        break
                break

        if not matched:
            validation_errors.append(
                f"  [{idx}] target={target_field}: "
                f"table '{table_name}' not found in any explore report"
            )

    if validation_errors:
        console.print("[yellow]Validation warnings:[/]")
        for err in validation_errors:
            console.print(f"  {err}")
        if not force:
            console.print(
                "[red]Validation failed. Use --force to approve anyway, or fix the issues above.[/]"
            )
            raise typer.Exit(1)
        console.print("[yellow]Proceeding with --force (validation errors ignored).[/]")

    if not feasible and not force:
        console.print("[red]评估标记为不可行。使用 --force 强制通过，或先修复问题。[/]")
        raise typer.Exit(1)

    req.assessment = assessment
    # Keep IN_PROJECT status if re-approving a requirement already in a project
    if req.status != RequirementStatus.IN_PROJECT:
        req.status = RequirementStatus.APPROVED
    state.save_requirement(req)
    git_add_all(state.root)
    git_commit(f"feat(requirement): approve {req_id}", state.root)
    console.print(f"[green]审批通过:[/] {req_id} — 可以创建项目")
    console.print(f"  字段映射: {len(mappings)} 个")
    if risks:
        console.print(f"  风险项: {len(risks)} 个")


@app.command()
def update(
    req_id: str = typer.Argument(..., help="Requirement ID"),
    name: str = typer.Option(None, "--name", "-n"),
    background: str = typer.Option(None, "--background", "-b"),
    goal: str = typer.Option(None, "--goal", "-g"),
    target_schema: list[Path] = typer.Option(
        [], "--target-schema", "-s", help="Path to target schema YAML file(s) or directory"
    ),
) -> None:
    """Update a requirement. Resets assessment status if schemas change."""
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    if name:
        req.name = name
    if background is not None:
        req.background = background
    if goal is not None:
        req.goal = goal

    if target_schema:
        schema_paths: list[Path] = []
        for path in target_schema:
            if path.is_dir():
                schema_paths.extend(sorted(path.glob("*.yaml")))
                schema_paths.extend(sorted(path.glob("*.yml")))
            elif path.exists():
                schema_paths.append(path)
            else:
                console.print(f"[red]Path not found:[/] {path}")
                raise typer.Exit(1)

        req.target_schemas = []
        for sp in schema_paths:
            data = yaml.safe_load(sp.read_text())
            req.target_schemas.append(TargetSchema.model_validate(data))

        # Reset assessment when target schema changes
        req.assessment = None
        req.status = RequirementStatus.DRAFT

    state.save_requirement(req)
    git_add_all(state.root)
    git_commit(f"feat(requirement): update {req_id}", state.root)
    console.print(f"[green]Updated requirement:[/] {req_id}")


@app.command("add-schema")
def add_schema(
    req_id: str = typer.Argument(..., help="Requirement ID"),
    schema_paths: list[Path] = typer.Argument(
        ..., help="Path(s) to target schema YAML file(s), directory, or CSV file"
    ),
) -> None:
    """Add target schema(s) to an existing requirement.

    Supports YAML files, directories of YAML files, or CSV files.
    Schema files are saved into requirements/{id}/target_schema/ for versioning.
    Adding schemas resets any existing assessment.

    Examples:
        antline requirement add-schema REQ-20260508-001 target_schema/patients.yaml
        antline requirement add-schema REQ-20260508-001 target_schema/hosp/
        antline requirement add-schema REQ-20260508-001 standard_schema.csv
    """
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    # Ensure target_schema directory exists
    target_schema_dir = state.root / "requirements" / req_id / "target_schema"
    target_schema_dir.mkdir(parents=True, exist_ok=True)

    new_schemas: list[TargetSchema] = []
    saved_paths: list[Path] = []

    for path in schema_paths:
        if not path.exists():
            console.print(f"[red]Path not found:[/] {path}")
            raise typer.Exit(1)

        if path.suffix.lower() == ".csv":
            # Import from CSV
            csv_schemas = import_schema_from_csv(path)
            for schema in csv_schemas:
                dest = target_schema_dir / f"{schema.table}.yaml"
                save_schemas_as_yaml([schema], target_schema_dir)
                new_schemas.append(schema)
                saved_paths.append(dest)
        elif path.is_dir():
            for sp in sorted(path.glob("*.yaml")):
                data = yaml.safe_load(sp.read_text())
                schema = TargetSchema.model_validate(data)
                dest = target_schema_dir / sp.name
                shutil.copy2(sp, dest)
                new_schemas.append(schema)
                saved_paths.append(dest)
            for sp in sorted(path.glob("*.yml")):
                data = yaml.safe_load(sp.read_text())
                schema = TargetSchema.model_validate(data)
                dest = target_schema_dir / sp.name
                shutil.copy2(sp, dest)
                new_schemas.append(schema)
                saved_paths.append(dest)
        else:
            data = yaml.safe_load(path.read_text())
            schema = TargetSchema.model_validate(data)
            dest = target_schema_dir / path.name
            shutil.copy2(path, dest)
            new_schemas.append(schema)
            saved_paths.append(dest)

    if not new_schemas:
        console.print("[yellow]No schema files found.[/]")
        raise typer.Exit(0)

    # Append to existing schemas (avoid duplicates by table name)
    existing_tables = {s.table for s in req.target_schemas}
    appended = 0
    for schema in new_schemas:
        if schema.table not in existing_tables:
            req.target_schemas.append(schema)
            existing_tables.add(schema.table)
            appended += 1
        else:
            console.print(f"[yellow]Skipped duplicate table:[/] {schema.table}")

    if appended == 0:
        console.print("[yellow]No new schemas added (all tables already exist).[/]")
        raise typer.Exit(0)

    # Reset assessment when target schema changes
    if req.assessment:
        req.assessment = None
        req.status = RequirementStatus.DRAFT
        console.print(f"[yellow]Assessment reset:[/] schemas changed")

    state.save_requirement(req)
    git_add_all(state.root)
    git_commit(f"feat(requirement): add schema(s) to {req_id}", state.root)

    console.print(f"[green]Added {appended} schema(s) to requirement:[/] {req_id}")
    for s in new_schemas[:appended]:
        console.print(f"  - {s.table} ({len(s.fields)} fields)")
    if saved_paths:
        console.print(f"  Saved to: {target_schema_dir}")


@app.command()
def remove(
    req_id: str = typer.Argument(..., help="Requirement ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a requirement."""
    state = ProjectState()
    req = state.get_requirement(req_id)
    if not req:
        console.print(f"[red]Requirement not found:[/] {req_id}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Remove requirement {req_id} ({req.name})?")
        if not confirm:
            console.print("[yellow]Cancelled.[/]")
            raise typer.Exit(0)

    state.delete_requirement(req_id)
    git_add_all(state.root)
    git_commit(f"feat(requirement): remove {req_id}", state.root)
    console.print(f"[green]Removed requirement:[/] {req_id}")
