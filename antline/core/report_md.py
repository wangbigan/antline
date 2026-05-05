"""Markdown report formatter for human-readable output."""

from __future__ import annotations

from antline.core.models import SourceExploreReport


def _pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def _fmt_sample(data: list) -> str:
    if not data:
        return "-"
    samples = [str(v) if v is not None else "NULL" for v in data[:3]]
    return ", ".join(samples)


def render_explore_report(report: SourceExploreReport) -> str:
    """Render a SourceExploreReport as Markdown."""
    lines: list[str] = []
    summary = report.summary

    lines.append(f"# Source Explore Report: {report.source_id}")
    lines.append("")
    lines.append(f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8 Beijing)")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Database | {summary.get('database', 'N/A')} ({summary.get('db_type', 'N/A')}) |")
    lines.append(f"| Tables | {summary.get('total_tables', 0):,} |")
    lines.append(f"| Total Rows | {summary.get('total_rows', 0):,} |")
    lines.append(f"| Total Columns | {summary.get('total_columns', 0):,} |")
    lines.append("")

    # Tables
    lines.append("## Tables")
    lines.append("")

    for table in report.tables:
        lines.append(f"### {table.name}")
        lines.append("")
        if table.comment:
            lines.append(f"*Table comment:* {table.comment}")
            lines.append("")

        lines.append(f"- **Schema:** {table.schema_name or '-'}  ")
        row_text = f"{table.row_count:,}" if table.row_count >= 0 else "?"
        lines.append(f"- **Rows:** {row_text}  ")
        lines.append(f"- **Columns:** {len(table.columns)}  ")
        lines.append(f"- **Primary Key:** {', '.join(table.primary_key) or '-'}  ")
        lines.append("")

        if not table.columns:
            lines.append("*No columns found.*")
            lines.append("")
            continue

        # Columns table
        lines.append("#### Columns")
        lines.append("")
        lines.append(
            "| Column | Type | Nullable | Default | Comment | Nulls | Unique | Sample |"
        )
        lines.append(
            "|--------|------|----------|---------|---------|-------|--------|--------|"
        )

        for col in table.columns:
            nullable = "YES" if col.nullable else "NO"
            default = col.default or "-"
            comment = col.comment or "-"
            stats = col.stats
            nulls = f"{stats.null_count:,} ({_pct(stats.null_rate)})" if stats.null_count > 0 else "0"
            unique = "✓" if stats.is_unique_candidate else "-"
            sample = _fmt_sample(col.sample_data)
            lines.append(
                f"| {col.name} | {col.data_type} | {nullable} | {default} | {comment} | {nulls} | {unique} | {sample} |"
            )

        lines.append("")

        # Top-N values (if any)
        for col in table.columns:
            if col.stats.topn_values:
                lines.append(f"**{col.name}** top values:")
                lines.append("")
                lines.append("| Value | Count | Frequency |")
                lines.append("|-------|-------|-----------|")
                for item in col.stats.topn_values[:10]:
                    val = str(item.get("value", "")) if item.get("value") is not None else "NULL"
                    count = item.get("count", 0)
                    freq = _pct(item.get("frequency", 0))
                    lines.append(f"| {val} | {count:,} | {freq} |")
                lines.append("")

    return "\n".join(lines)
