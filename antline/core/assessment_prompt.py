"""Generate assessment prompts and templates for human/LLM review."""

from __future__ import annotations

from antline.core.models import (
    ColumnMeta,
    Requirement,
    SourceExploreReport,
    TableMeta,
)


def _format_field_basic(col: ColumnMeta) -> str:
    """Format column with name, type, nullable, comment only."""
    parts = [f"- `{col.name}`: {col.data_type}"]
    if not col.nullable:
        parts.append("NOT NULL")
    if col.comment:
        parts.append(f"— {col.comment}")
    return " ".join(parts)


def _format_field_full(col: ColumnMeta) -> str:
    """Format column with full statistics."""
    lines = [f"- `{col.name}`: {col.data_type}"]
    if not col.nullable:
        lines.append("  - 可空: 否")
    if col.comment:
        lines.append(f"  - 注释: {col.comment}")
    stats = col.stats
    lines.append(f"  - 空值率: {stats.null_rate:.2%} ({stats.null_count} 个空值)")
    lines.append(f"  - 唯一值数: {stats.unique_count}")
    if stats.is_unique_candidate:
        lines.append("  - 唯一性候选: 是")
    if stats.min_value is not None:
        lines.append(f"  - 最小值: {stats.min_value}")
    if stats.max_value is not None:
        lines.append(f"  - 最大值: {stats.max_value}")
    if stats.topn_values:
        top = ", ".join(
            f"{v.get('value', '?')}({v.get('count', 0)})"
            for v in stats.topn_values[:5]
        )
        lines.append(f"  - 高频值: {top}")
    if col.sample_data:
        samples = ", ".join(str(s) for s in col.sample_data[:3])
        lines.append(f"  - 样例: {samples}")
    return "\n".join(lines)


def _format_table_basic(table: TableMeta) -> str:
    """Format table with basic info (no stats)."""
    lines = [f"### {table.name}"]
    if table.comment:
        lines.append(f"*注释: {table.comment}*")
    lines.append(f"- 行数: {table.row_count}")
    lines.append(f"- 字段数: {len(table.columns)}")
    if table.primary_key:
        lines.append(f"- 主键: {', '.join(table.primary_key)}")
    lines.append("")
    lines.append("**字段列表:**")
    for col in table.columns:
        lines.append(_format_field_basic(col))
    lines.append("")
    return "\n".join(lines)


def _format_table_full(table: TableMeta) -> str:
    """Format table with full statistics."""
    lines = [f"### {table.name}"]
    if table.comment:
        lines.append(f"*注释: {table.comment}*")
    lines.append(f"- 行数: {table.row_count}")
    lines.append(f"- 字段数: {len(table.columns)}")
    if table.primary_key:
        lines.append(f"- 主键: {', '.join(table.primary_key)}")
    lines.append("")
    lines.append("**字段列表（含统计信息）:**")
    for col in table.columns:
        lines.append(_format_field_full(col))
    lines.append("")
    return "\n".join(lines)


def generate_llm_prompt(
    req: Requirement,
    reports: list[SourceExploreReport],
    focus_tables: list[str] | None = None,
    full_stats: bool = False,
) -> str:
    """Generate an LLM prompt for feasibility assessment.

    Args:
        req: The requirement to assess.
        reports: Source explore reports.
        focus_tables: Only include these source tables (by name). If None, include all.
        full_stats: Include field-level statistics.
    """
    lines: list[str] = []

    lines.append("# 数据需求可行性评估")
    lines.append("")
    lines.append(f"**需求编号:** {req.id}")
    lines.append(f"**需求名称:** {req.name}")
    if req.background:
        lines.append(f"**业务背景:** {req.background}")
    if req.goal:
        lines.append(f"**目标:** {req.goal}")
    lines.append("")

    # Target schemas
    lines.append("## 目标数据标准")
    lines.append("")
    for schema in req.target_schemas:
        lines.append(f"### 目标表: `{schema.table}`")
        if schema.description:
            lines.append(f"{schema.description}")
        lines.append("")
        lines.append("| 字段名 | 数据类型 | 可空 | 说明 |")
        lines.append("|--------|----------|------|------|")
        for f in schema.fields:
            null_str = "是" if f.nullable else "否"
            lines.append(f"| {f.name} | {f.data_type} | {null_str} | {f.description} |")
        lines.append("")

    # Source metadata
    lines.append("## 源数据库元数据")
    lines.append("")

    for report in reports:
        lines.append(f"### 数据源: {report.source_id}")
        lines.append("")

        tables = report.tables
        if focus_tables:
            tables = [t for t in tables if t.name in focus_tables]

        if not tables:
            lines.append("*未找到匹配的表。*")
            lines.append("")
            continue

        for table in tables:
            if full_stats:
                lines.append(_format_table_full(table))
            else:
                lines.append(_format_table_basic(table))

    # Instructions
    lines.append("---")
    lines.append("")
    lines.append("## 任务：评估可行性")
    lines.append("")
    lines.append("请评估上述目标数据标准能否通过源数据库满足。")
    lines.append("")
    lines.append("### 输出要求")
    lines.append("")
    lines.append("请直接输出一份 **Markdown 格式的可行性评估报告**，格式如下：")
    lines.append("")
    lines.append("```markdown")
    lines.append("---")
    lines.append("feasible: true  # 是否可行 (true/false)")
    lines.append("source_ids: [SRC-001]  # 参与评估的数据源ID列表")
    lines.append("field_mappings:")
    lines.append("  - target_field: 目标表.目标字段")
    lines.append("    source_table: 源表名（缺失填 null）")
    lines.append("    source_field: 源字段名（缺失填 null）")
    lines.append("    mapping_type: direct(直接) | transform(转换) | missing(缺失) | merge(合并)")
    lines.append("    transform_logic: 转换逻辑描述")
    lines.append("    risk: low(低) | medium(中) | high(高) | critical(严重)")
    lines.append("risks:")
    lines.append("  - level: low | medium | high | critical")
    lines.append("    description: 风险描述")
    lines.append("    target_field: 受影响的目标字段（可选）")
    lines.append("    source_table: 受影响的源表（可选）")
    lines.append("notes: 其他备注")
    lines.append("---")
    lines.append("")
    lines.append("# 可行性评估报告")
    lines.append("")
    lines.append("## 结论")
    lines.append("可行性：是 / 否")
    lines.append("")
    lines.append("## 字段映射")
    lines.append("")
    lines.append("| 目标字段 | 源表 | 源字段 | 映射类型 | 风险 |")
    lines.append("|----------|------|--------|----------|------|")
    lines.append("| ... | ... | ... | ... | ... |")
    lines.append("")
    lines.append("## 风险分析")
    lines.append("")
    lines.append("...")
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.append("...")
    lines.append("```")
    lines.append("")
    lines.append("**注意：**")
    lines.append("- 文件顶部的 `---` 块（YAML frontmatter）包含结构化数据，供系统解析")
    lines.append("- 正文部分是人类可读的分析，建议包含结论、字段映射表格、风险分析和备注")
    lines.append("- 将输出保存为 `.md` 文件（如 `assessment.md`），人工审核微调后用于审批")
    lines.append("")

    return "\n".join(lines)


def generate_human_guide(
    req: Requirement,
    reports: list[SourceExploreReport],
    focus_tables: list[str] | None = None,
    full_stats: bool = False,
) -> str:
    """Generate a human-readable assessment guide."""
    lines: list[str] = []

    lines.append("# 评估操作指南")
    lines.append("")
    lines.append(f"**需求:** {req.id} — {req.name}")
    lines.append("")
    lines.append("## 工作流程")
    lines.append("")
    lines.append("1. 将 `prompt.md` 的内容复制给大模型（GPT/Claude/通义等）")
    lines.append("2. 大模型输出 Markdown 格式的评估报告（含 YAML frontmatter）")
    lines.append("3. 人工审核报告内容，做必要的微调修改")
    lines.append("4. 保存为 `assessment.md`")
    lines.append("5. 运行: `antline requirement approve REQ-001`")
    lines.append("")
    lines.append("## 文件格式说明")
    lines.append("")
    lines.append("评估报告使用 **Markdown + YAML frontmatter** 格式：")
    lines.append("")
    lines.append("```markdown")
    lines.append("---")
    lines.append("feasible: true")
    lines.append("field_mappings:")
    lines.append("  - target_field: patients.subject_id")
    lines.append("    source_table: patient_info")
    lines.append("    source_field: subject_id")
    lines.append("    mapping_type: direct")
    lines.append("    risk: low")
    lines.append("---")
    lines.append("")
    lines.append("# 可行性评估报告")
    lines.append("...")
    lines.append("```")
    lines.append("")
    lines.append("- 顶部 `---` 块是结构化数据，`approve` 命令解析这部分")
    lines.append("- 正文是 Markdown 格式的可读分析，便于人工审核")
    lines.append("")

    lines.append("## 目标数据标准")
    lines.append("")
    for schema in req.target_schemas:
        lines.append(f"### {schema.table}")
        if schema.description:
            lines.append(f"{schema.description}")
        lines.append("")
        for f in schema.fields:
            null_str = "可空" if f.nullable else "非空"
            lines.append(f"- `{f.name}` ({f.data_type}, {null_str}): {f.description}")
        lines.append("")

    lines.append("## 源数据库元数据")
    lines.append("")
    for report in reports:
        lines.append(f"### 数据源: {report.source_id}")
        lines.append("")

        tables = report.tables
        if focus_tables:
            tables = [t for t in tables if t.name in focus_tables]

        if not tables:
            lines.append("*未找到匹配的表。*")
            lines.append("")
            continue

        for table in tables:
            if full_stats:
                lines.append(_format_table_full(table))
            else:
                lines.append(_format_table_basic(table))

    lines.append("---")
    lines.append("")
    lines.append("## 映射类型速查表")
    lines.append("")
    lines.append("| 映射类型 | 含义 | 示例 |")
    lines.append("|----------|------|------|")
    lines.append("| `direct` | 源字段与目标字段一一对应 | `source.id` → `target.patient_id` |")
    lines.append("| `transform` | 需要计算或转换 | `CONCAT(first, last)` → `target.full_name` |")
    lines.append("| `missing` | 源端没有对应字段 | `NULL` → `target.dod` |")
    lines.append("| `merge` | 需要合并多个来源 | `source_a.id + source_b.id` → `target.id` |")
    lines.append("")

    return "\n".join(lines)


def generate_assessment_template(req: Requirement, source_ids: list[str]) -> str:
    """Generate an empty assessment template as Markdown + YAML frontmatter."""
    import yaml

    mappings = []
    for schema in req.target_schemas:
        for field in schema.fields:
            mappings.append({
                "target_field": f"{schema.table}.{field.name}",
                "source_table": None,
                "source_field": None,
                "mapping_type": "missing",
                "transform_logic": "",
                "risk": "high",
            })

    frontmatter = {
        "feasible": False,
        "source_ids": source_ids,
        "field_mappings": mappings,
        "risks": [],
        "notes": "",
    }

    yaml_block = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)

    lines = [
        "---",
        yaml_block.rstrip(),
        "---",
        "",
        f"# 可行性评估报告：{req.id}",
        "",
        "## 结论",
        "",
        "可行性：否（待评估）",
        "",
        "## 字段映射",
        "",
        "| 目标字段 | 源表 | 源字段 | 映射类型 | 风险 |",
        "|----------|------|--------|----------|------|",
    ]
    for m in mappings:
        lines.append(f"| {m['target_field']} | - | - | missing | high |")
    lines.extend([
        "",
        "## 风险分析",
        "",
        "（待补充）",
        "",
        "## 备注",
        "",
        "（待补充）",
        "",
    ])

    return "\n".join(lines)
