"""Data requirement management commands."""

from __future__ import annotations

import json
import re
import shutil
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from antline.core.analysis_skill import AnalysisResult
from antline.core.config import ProjectState
from antline.core.csv_schema import import_schema_from_csv, save_schemas_as_yaml
from antline.core.git import git_add_all, git_commit
from antline.core.llm_config import LLMConfig, create_llm_call, load_llm_config
from antline.core.models import (
    AssessmentRisk,
    CleanRule,
    FieldMapping,
    Requirement,
    RequirementAssessment,
    RequirementStatus,
    SourceExploreReport,
    TargetSchema,
)
from antline.core.sql_validator import validate_all_model_sqls

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
    auto: bool = typer.Option(False, "--auto", help="使用LLM自动分析生成映射和SQL"),
    step: str = typer.Option(
        "",
        "--step",
        help="仅执行特定步骤: scope (表级分析), generate (生成SQL，需配合--scope-file)",
    ),
    scope_file: Path = typer.Option(None, "--scope-file", help="传入已有的scope JSON文件"),
    json_output: bool = typer.Option(False, "--json", help="输出JSON格式结果"),
    min_confidence: float = typer.Option(
        0.0, "--min-confidence", help="仅显示置信度高于此值的映射"
    ),
    validate_sql_flag: bool = typer.Option(
        False, "--validate", help="对生成的 SQL 在本地目标数据库执行校验 (需已运行 source setup)"
    ),
    target_password: str = typer.Option(
        "", "--target-password", help="本地目标数据库密码 (仅 --validate 时使用)"
    ),
) -> None:
    """Generate assessment materials for a requirement.

    Default mode (no --auto): produces prompt.md + guide.md + template.md for
    manual / external LLM review.

    Auto mode (--auto): runs the DataRequirementAnalysisSkill pipeline to
    automatically generate field mappings, model SQL, and clean rules.

    Examples:
        antline requirement assess REQ-001 SRC-001
        antline requirement assess REQ-001 SRC-001 --auto
        antline requirement assess REQ-001 SRC-001 --auto --step scope
        antline requirement assess REQ-001 SRC-001 --auto --step generate --scope-file scope.json
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

    # Load explore reports
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

    # ------------------------------------------------------------------
    # Auto mode: LLM-driven analysis
    # ------------------------------------------------------------------
    if auto:
        console.print(f"[bold]自动分析[/] {req_id} …")

        # Try to initialise an LLM client
        try:
            from antline.core.analysis_skill import (
                AnalysisResult,
                DataRequirementAnalysisSkill,
            )

            llm_config = load_llm_config(state.root / "antline.yml")
            if llm_config is None:
                llm_config = LLMConfig()  # defaults: openai / gpt-4o
            llm_call = create_llm_call(llm_config)
            console.print(
                f"  [dim]LLM: {llm_config.provider} / {llm_config.model}[/]"
            )
        except RuntimeError as exc:
            console.print(f"[red]LLM 未配置:[/] {exc}")
            console.print(
                "[yellow]提示:[/] 在 antline.yml 中配置 llm 节，"
                "或设置 OPENAI_API_KEY / ANTHROPIC_API_KEY 环境变量。"
            )
            raise typer.Exit(1) from None
        except Exception as exc:
            console.print(f"[red]初始化 LLM 失败:[/] {exc}")
            raise typer.Exit(1) from exc

        def _progress(step: str, msg: str, data: Any = None) -> None:
            if step == "step1" and "raw" in (data or {}) or step in ("step2", "step4") and "raw" in (data or {}):
                raw = data["raw"]
                console.print(f"  [dim]{msg}[/]")
                if raw and len(raw) > 0:
                    preview = raw[:400] + ("…" if len(raw) > 400 else "")
                    console.print(f"    [dim]{preview}[/]")
                else:
                    console.print("    [red](空响应)[/]")
            elif step == "step3" or step == "step5":
                uncovered = (data or {}).get("uncovered", [])
                if uncovered:
                    console.print(f"  [yellow]{msg}[/]")
                else:
                    console.print(f"  [dim]{msg}[/]")
            else:
                console.print(f"  [dim]{msg}[/]")

        skill = DataRequirementAnalysisSkill(llm_call=llm_call, progress_callback=_progress)

        # Partial step execution
        if step == "scope":
            # Only run Step 1
            source_text = "\n\n".join(
                _summarize_report(r) for r in reports
            )
            scope = skill._step1_table_scope(req.target_schemas, source_text)

            if json_output:
                console.print(json.dumps(scope, ensure_ascii=False, indent=2))
            else:
                console.print("[green]表级分析完成:[/]")
                for tbl, info in scope.items():
                    conf = info.get("confidence", 0.0)
                    primary = info.get("primary_source", {}).get("table", "N/A")
                    console.print(f"  {tbl}: primary={primary}, confidence={conf:.2f}")

            # Save scope to assessment dir
            assessment_dir = state.root / "requirements" / req_id / "assessment"
            assessment_dir.mkdir(parents=True, exist_ok=True)
            scope_path = assessment_dir / "scope.json"
            scope_path.write_text(
                json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            console.print(f"  范围分析已保存: {scope_path}")
            return

        if step == "generate":
            # Steps 2-5 with optional pre-loaded scope
            if scope_file and scope_file.exists():
                scope = json.loads(scope_file.read_text())
            else:
                # Run step 1 first
                source_text = "\n\n".join(
                    _summarize_report(r) for r in reports
                )
                scope = skill._step1_table_scope(req.target_schemas, source_text)

            result = AnalysisResult(source_scope=scope)
            source_text = "\n\n".join(
                _summarize_report(r) for r in reports
            )

            for ts in req.target_schemas:
                table_scope = scope.get(ts.table, {})
                if not table_scope:
                    continue
                step2 = skill._step2_generate_sql(ts, table_scope, source_text)
                map_sql = step2.get("map_sql", "")
                uncovered = _audit_coverage(map_sql, [f.name for f in ts.fields])
                if uncovered:
                    gap_fill = skill._step4_gap_fill(uncovered, ts.table, source_text)
                    map_sql = _merge_gaps_into_sql(map_sql, gap_fill)
                    uncovered = _audit_coverage(map_sql, [f.name for f in ts.fields])
                result.model_sqls[ts.table] = map_sql
                result.uncovered_fields.extend(f"{ts.table}.{f}" for f in uncovered)
                for cr_data in step2.get("clean_rules", []):
                    with suppress(Exception):
                        result.clean_rules.append(CleanRule.model_validate(cr_data))

            _save_auto_assessment(state, req, result, source_ids)
            _print_auto_result(result, json_output, min_confidence)
            if validate_sql_flag:
                _run_sql_validation(state, result.model_sqls, target_password)
            return

        # Full pipeline (steps 0-5)
        result = skill.analyze(req, reports)
        _save_auto_assessment(state, req, result, source_ids)
        _print_auto_result(result, json_output, min_confidence)
        if validate_sql_flag:
            _run_sql_validation(state, result.model_sqls, target_password)
        return

    # ------------------------------------------------------------------
    # Default mode: generate prompts for manual/external review
    # ------------------------------------------------------------------
    focus_tables = [t.strip() for t in focus.split(",") if t.strip()] or None

    console.print(f"[bold]正在生成评估材料[/] {req_id} …")

    from antline.core.assessment_prompt import (
        generate_assessment_template,
        generate_human_guide,
        generate_llm_prompt,
    )

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


def _summarize_report(report: SourceExploreReport) -> str:
    """Local copy of the summariser so we don't need to import it here."""
    from antline.core.analysis_skill import _summarize_report as fn
    return fn(report)


def _audit_coverage(sql: str, target_fields: list[str]) -> list[str]:
    """Local wrapper for coverage audit."""
    from antline.core.analysis_skill import _audit_coverage as fn
    return fn(sql, target_fields)


def _merge_gaps_into_sql(base_sql: str, gap_fill: list[dict[str, Any]]) -> str:
    """Local wrapper for SQL merge."""
    from antline.core.analysis_skill import _merge_gaps_into_sql as fn
    return fn(base_sql, gap_fill)


def _save_auto_assessment(
    state: ProjectState,
    req: Requirement,
    result: AnalysisResult,
    source_ids: list[str],
) -> None:
    """Persist an auto-generated assessment into the requirement."""

    assessment = RequirementAssessment(
        feasible=True,
        report_path="auto-generated",
        source_ids=list(source_ids),
        field_mappings=result.field_mappings,
        clean_rules=result.clean_rules,
        risks=result.risks,
        notes="由 DataRequirementAnalysisSkill 自动评估",
        assessed_at=datetime.now(timezone(timedelta(hours=8))),
        engine_version="2.0-llm",
        auto_assessed=True,
        model_sqls=result.model_sqls,
        source_scope=result.source_scope,
    )
    req.assessment = assessment
    req.status = RequirementStatus.ASSESSED
    state.save_requirement(req)

    # Save artifacts to assessment dir for human review
    assessment_dir = state.root / "requirements" / req.id / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)

    scope_path = assessment_dir / "scope.json"
    scope_path.write_text(
        json.dumps(result.source_scope, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for model_name, sql in result.model_sqls.items():
        sql_path = assessment_dir / f"{model_name}.sql"
        sql_path.write_text(sql, encoding="utf-8")

    git_add_all(state.root)
    git_commit(f"feat(requirement): auto-assess {req.id}", state.root)


def _print_auto_result(
    result: AnalysisResult,
    json_output: bool,
    min_confidence: float,
) -> None:
    """Print analysis result to console."""

    if json_output:
        console.print(
            json.dumps(
                {
                    "source_scope": result.source_scope,
                    "model_sqls": result.model_sqls,
                    "field_mappings": [
                        m.model_dump(mode="json")
                        for m in result.field_mappings
                        if m.confidence >= min_confidence
                    ],
                    "clean_rules": [cr.model_dump(mode="json") for cr in result.clean_rules],
                    "uncovered_fields": result.uncovered_fields,
                    "confidence": result.confidence,
                    "approval_recommendation": result.approval_recommendation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    _rec_map = {"auto": "自动通过", "suggest": "建议人工审核", "manual": "必须人工审核"}
    rec_cn = _rec_map.get(result.approval_recommendation, result.approval_recommendation)
    console.print("[green]自动分析完成[/]")
    console.print(f"  置信度: {result.confidence:.2f}")
    console.print(f"  建议: {rec_cn}")
    console.print(f"  模型SQL: {len(result.model_sqls)} 个")
    if result.uncovered_fields:
        console.print(f"  [yellow]未覆盖字段: {len(result.uncovered_fields)} 个[/]")
        for f in result.uncovered_fields:
            console.print(f"    - {f}")
    if result.risks:
        console.print(f"  风险: {len(result.risks)} 个")
        for r in result.risks:
            console.print(f"    [{r.level}] {r.description}")


def _run_sql_validation(
    state: ProjectState,
    model_sqls: dict[str, str],
    target_password: str,
) -> None:
    """Run SQL validation for generated model SQLs against local target DB."""
    platform = state.workspace_platform()
    if not platform:
        console.print("[yellow]跳过 SQL 校验:[/] Workspace platform 未配置")
        return

    db_type = platform.get("db_type", "postgresql")
    if db_type != "postgresql":
        console.print(f"[yellow]跳过 SQL 校验:[/] 仅支持 PostgreSQL 目标, 当前为 {db_type}")
        return

    host = platform.get("host", "localhost")
    port = platform.get("port", 5432)
    database = platform.get("database", "")
    platform_user = platform.get("user", "")

    if not database:
        console.print("[yellow]跳过 SQL 校验:[/] 目标数据库名未配置")
        return

    if not platform_user:
        platform_user = typer.prompt("Target database user")
    if not target_password:
        target_password = typer.prompt("Target database password", hide_input=True)

    console.print(f"\n[bold]SQL 语法校验[/] ({host}:{port}/{database}) …")
    val_results = validate_all_model_sqls(
        state=state,
        model_sqls=model_sqls,
        target_user=platform_user,
        target_password=target_password,
        target_db=database,
    )

    if "error" in val_results:
        console.print(f"  [red]校验失败:[/] {val_results['error']}")
        return

    total = len(val_results)
    passed = sum(1 for r in val_results.values() if r.get("success"))
    failed = total - passed

    for model_name, r in val_results.items():
        if r.get("success"):
            console.print(f"  [green]✓[/] {model_name}: {r['message']}")
        else:
            console.print(f"  [red]✗[/] {model_name}: {r['message']}")
            if r.get("error"):
                console.print(f"      {r['error']}")

    summary = f"{passed}/{total} 通过"
    if failed > 0:
        summary += f", {failed} 失败"
    console.print(f"  校验汇总: {summary}")


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

    # ------------------------------------------------------------------
    # Auto-assessed shortcut: if assessment already in requirement.yml,
    # skip the file parsing
    # ------------------------------------------------------------------
    if not assessment_file.exists() and req.assessment:
        console.print(f"[dim]使用已有自动评估结果 (engine={req.assessment.engine_version})[/]")
        assessment = req.assessment
        # Ensure reapproval_reason is set if needed
        if note:
            assessment.reapproval_reason = note
            assessment.assessed_at = datetime.now(timezone(timedelta(hours=8)))
    else:
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
            raise typer.Exit(1) from exc

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
            clean_rules = [CleanRule.model_validate(cr) for cr in data.get("clean_rules", [])]
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
                clean_rules=clean_rules,
                risks=risks,
                notes=data.get("notes", ""),
                reapproval_reason=note,
                assessed_at=datetime.now(timezone(timedelta(hours=8))),
            )
        except Exception as exc:
            console.print(f"[red]评估数据无效:[/] {exc}")
            raise typer.Exit(1) from exc

    # ------------------------------------------------------------------
    # Validate table/field references against explore reports
    # ------------------------------------------------------------------
    validation_errors: list[str] = []

    # Load all explore reports referenced by source_ids
    explore_reports: dict[str, SourceExploreReport] = {}
    for sid in assessment.source_ids:
        report_path = state.root / "sources" / sid / "explore" / "report.yml"
        if report_path.exists():
            report_data = yaml.safe_load(report_path.read_text())
            if report_data:
                explore_reports[sid] = SourceExploreReport.model_validate(report_data)

    for idx, m in enumerate(assessment.field_mappings, start=1):
        if m.mapping_type == "missing":
            continue

        source_table = m.source_table or ""
        source_field = m.source_field or ""
        target_field = m.target_field

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

    if not assessment.feasible and not force:
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
    console.print(f"  字段映射: {len(assessment.field_mappings)} 个")
    if assessment.risks:
        console.print(f"  风险项: {len(assessment.risks)} 个")


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
        console.print("[yellow]Assessment reset:[/] schemas changed")

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
