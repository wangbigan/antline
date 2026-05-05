"""Markdown formatter for assessment reports (human review)."""

from __future__ import annotations

from antline.core.models import Requirement, RequirementAssessment


def render_assessment_report(req: Requirement) -> str:
    """Render a RequirementAssessment as Markdown for human review."""
    a = req.assessment
    if not a:
        return ""

    lines: list[str] = []

    lines.append(f"# Feasibility Assessment: {req.id}")
    lines.append("")
    lines.append(f"**Requirement:** {req.name}")
    if req.background:
        lines.append(f"**Background:** {req.background}")
    if req.goal:
        lines.append(f"**Goal:** {req.goal}")
    lines.append(f"**Status:** {'FEASIBLE' if a.feasible else 'NOT FEASIBLE'} (draft)")
    lines.append(f"**Sources:** {', '.join(a.source_ids)}")
    if a.assessed_at:
        lines.append(f"**Assessed at:** {a.assessed_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Review instructions
    lines.append("---")
    lines.append("")
    lines.append("> **Review Instructions:** Edit the tables below to fix mappings or adjust risk levels. "
                 "After review, save this file without the `_template` suffix (as `.md`) "
                 "and run: `antline requirement approve {req.id}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Field Mappings
    lines.append("## Field Mappings")
    lines.append("")
    lines.append("| # | Target Field | Source Table | Source Field | Mapping Type | Risk |")
    lines.append("|---|--------------|--------------|--------------|--------------|------|")
    for i, m in enumerate(a.field_mappings, 1):
        src_tbl = m.source_table or "-"
        src_fld = m.source_field or "-"
        lines.append(
            f"| {i} | {m.target_field} | {src_tbl} | {src_fld} | {m.mapping_type} | {m.risk} |"
        )
    lines.append("")

    # Risks
    if a.risks:
        lines.append("## Risks")
        lines.append("")
        lines.append("| # | Level | Description | Target Field | Source Table |")
        lines.append("|---|-------|-------------|--------------|--------------|")
        for i, r in enumerate(a.risks, 1):
            tgt = r.target_field or "-"
            src = r.source_table or "-"
            lines.append(f"| {i} | {r.level} | {r.description} | {tgt} | {src} |")
        lines.append("")

    # Notes area
    lines.append("## Reviewer Notes")
    lines.append("")
    lines.append("<!-- Add your notes here -->")
    lines.append("")

    return "\n".join(lines)
