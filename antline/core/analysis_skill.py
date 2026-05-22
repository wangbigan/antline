"""DataRequirementAnalysisSkill — LLM-driven 5-step requirement analysis.

Produces model-level SQL (map layer) plus clean rules, with a
"generate → audit → patch" feedback loop for high coverage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import sqlparse
from pydantic import BaseModel, Field

from antline.core.models import (
    AssessmentRisk,
    CleanRule,
    FieldMapping,
    Requirement,
    SourceExploreReport,
    TargetSchema,
)


class AnalysisResult(BaseModel):
    """Output of DataRequirementAnalysisSkill."""

    source_scope: dict[str, Any] = Field(default_factory=dict)
    model_sqls: dict[str, str] = Field(default_factory=dict)
    field_mappings: list[FieldMapping] = Field(default_factory=list)
    clean_rules: list[CleanRule] = Field(default_factory=list)
    risks: list[AssessmentRisk] = Field(default_factory=list)
    uncovered_fields: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    approval_recommendation: str = "manual"  # auto | suggest | manual


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class DataRequirementAnalysisSkill:
    """Analyse a requirement against explored sources and produce SQL.

    The skill runs a 5-step pipeline:
      0. Summarise source reports into LLM-friendly text.
      1. Table-scope analysis  → which source tables feed each target table.
      2. Model SQL generation  → full dbt model SQL for each target table.
      3. Coverage audit        → AST parse SQL, diff against target schema.
      4. Gap-fill search       → find mappings for uncovered fields.
      5. Model merge           → patch gaps back into the SQL.

    All LLM calls are injected via *llm_call* so the caller can choose the
    provider (OpenAI, Anthropic, local, mock for tests, …).
    """

    def __init__(
        self,
        llm_call: Callable[[str], str] | None = None,
        progress_callback: Callable[[str, str, Any], None] | None = None,
    ) -> None:
        self.llm_call = llm_call or self._default_llm_call
        self.progress_callback = progress_callback or (lambda _step, _msg, _data=None: None)

    # ------------------------------------------------------------------
    # Step 0–5 orchestration
    # ------------------------------------------------------------------

    def analyze(
        self,
        requirement: Requirement,
        reports: list[SourceExploreReport],
    ) -> AnalysisResult:
        """Run the full 5-step pipeline."""
        result = AnalysisResult()

        # --- Step 0: source summaries ----------------------------------
        source_summaries = [_summarize_report(r) for r in reports]
        all_source_text = "\n\n".join(source_summaries)
        self.progress_callback(
            "step0",
            f"Source summaries: {len(reports)} reports, {len(all_source_text)} chars",
            {"reports": len(reports), "chars": len(all_source_text)},
        )

        # --- Step 1: table scope analysis ------------------------------
        scope = self._step1_table_scope(
            requirement.target_schemas,
            all_source_text,
        )
        result.source_scope = scope
        result.confidence = _avg_scope_confidence(scope)
        self.progress_callback(
            "step1",
            f"Table scope analysis: {len(scope)} target tables identified",
            {"scope": scope},
        )

        for target_schema in requirement.target_schemas:
            target_table = target_schema.table
            table_scope = scope.get(target_table, {})
            if not table_scope:
                self.progress_callback(
                    "step1",
                    f"  [SKIP] No scope for {target_table}",
                    {"target_table": target_table},
                )
                result.risks.append(
                    AssessmentRisk(
                        level="high",
                        description=f"No source scope identified for {target_table}",
                        target_field=target_table,
                    )
                )
                continue

            self.progress_callback(
                "step2",
                f"Generating SQL for {target_table} …",
                {"target_table": target_table},
            )
            # --- Step 2: generate model SQL ----------------------------
            step2 = self._step2_generate_sql(
                target_schema,
                table_scope,
                all_source_text,
            )
            map_sql = step2.get("map_sql", "")
            clean_rules = step2.get("clean_rules", [])
            self.progress_callback(
                "step2",
                f"  SQL generated ({len(map_sql)} chars)",
                {"target_table": target_table, "sql": map_sql},
            )

            # --- Step 3: coverage audit --------------------------------
            target_fields = [f.name for f in target_schema.fields]
            uncovered = _audit_coverage(map_sql, target_fields)
            self.progress_callback(
                "step3",
                f"  Coverage audit: {len(target_fields) - len(uncovered)}/{len(target_fields)} fields covered",
                {"target_table": target_table, "uncovered": uncovered},
            )

            # --- Step 4: gap-fill (only if gaps found) -----------------
            if uncovered:
                self.progress_callback(
                    "step4",
                    f"  Gap-fill for {len(uncovered)} uncovered fields: {', '.join(uncovered)}",
                    {"target_table": target_table, "uncovered": uncovered},
                )
                gap_fill = self._step4_gap_fill(
                    uncovered,
                    target_table,
                    all_source_text,
                )
                # --- Step 5: merge gaps back into SQL ----------------
                map_sql = _merge_gaps_into_sql(map_sql, gap_fill)
                # Re-audit after merge
                uncovered = _audit_coverage(map_sql, target_fields)
                self.progress_callback(
                    "step5",
                    f"  Merged gaps, re-audit: {len(uncovered)} still uncovered",
                    {"target_table": target_table, "sql": map_sql, "uncovered": uncovered},
                )

            result.model_sqls[target_table] = map_sql
            result.uncovered_fields.extend(
                f"{target_table}.{f}" for f in uncovered
            )

            # Collect clean rules
            for cr_data in clean_rules:
                with suppress(Exception):
                    result.clean_rules.append(CleanRule.model_validate(cr_data))

            # Derive field_mappings from SQL for audit trail
            result.field_mappings.extend(
                _derive_field_mappings(map_sql, target_table, table_scope)
            )

        # Final approval recommendation
        if result.confidence >= 0.9 and not result.uncovered_fields:
            result.approval_recommendation = "auto"
        elif result.confidence >= 0.7 and len(result.uncovered_fields) <= 3:
            result.approval_recommendation = "suggest"
        else:
            result.approval_recommendation = "manual"

        return result

    # ------------------------------------------------------------------
    # LLM steps
    # ------------------------------------------------------------------

    def _step1_table_scope(
        self,
        target_schemas: list[TargetSchema],
        source_text: str,
    ) -> dict[str, Any]:
        """Return {target_table: {primary_source, join_sources, confidence}}."""
        target_desc = "\n".join(
            f"- {s.table}: {s.description or 'No description'} "
            f"(fields: {', '.join(f.name for f in s.fields)})"
            for s in target_schemas
        )
        prompt = (
            "You are a data architect. Based on the target data models and source "
            "database tables below, determine which source tables are needed to build "
            "each target table.\n\n"
            "=== TARGET MODELS ===\n"
            f"{target_desc}\n\n"
            "=== SOURCE TABLES ===\n"
            f"{source_text}\n\n"
            "Respond ONLY with valid JSON in this exact format:\n"
            "{\n"
            '  "target_table_name": {\n'
            '    "primary_source": {"table": "source_table_name", "confidence": 0.95},\n'
            '    "join_sources": [\n'
            '      {"table": "other_table", "join_key": "patient_id", "type": "left",\n'
            '       "fields": ["field1", "field2"]}\n'
            '    ],\n'
            '    "rationale": "brief explanation",\n'
            '    "confidence": 0.92\n'
            '  }\n'
            "}\n"
        )
        self.progress_callback(
            "step1", "  Prompting LLM for table scope analysis …", {"prompt_preview": prompt[:500]}
        )
        raw = self.llm_call(prompt)
        self.progress_callback("step1", "  LLM raw response:", {"raw": raw})
        return _safe_json_parse(raw, default={})

    def _step2_generate_sql(
        self,
        target_schema: TargetSchema,
        table_scope: dict[str, Any],
        source_text: str,
    ) -> dict[str, Any]:
        """Generate dbt model SQL + clean_rules for one target table."""
        fields_desc = "\n".join(
            f"- {f.name}: {f.data_type} "
            f"{'NOT NULL' if not f.nullable else 'nullable'}"
            f"{f' — {f.description}' if f.description else ''}"
            for f in target_schema.fields
        )

        primary = table_scope.get("primary_source", {}).get("table", "")
        joins = table_scope.get("join_sources", [])
        join_desc = ""
        if joins:
            join_desc = "\n".join(
                f"- JOIN {j.get('table')} ON {j.get('join_key')} ({j.get('type', 'left')} join)"
                for j in joins
            )

        prompt = (
            "You are a dbt data engineer. Write a dbt model SQL query that maps "
            f"source data to the target table '{target_schema.table}'.\n\n"
            f"=== TARGET SCHEMA: {target_schema.table} ===\n"
            f"{fields_desc}\n\n"
            "=== SOURCE TABLES (scoped) ===\n"
            f"Primary source: {primary}\n"
            f"{join_desc}\n\n"
            "=== FULL SOURCE CATALOG ===\n"
            f"{source_text}\n\n"
            "Requirements:\n"
            "1. Use dbt source() syntax: {{ source('<SOURCE_ID>', 'table_name') }}.\n"
            "   Use the EXACT source IDs shown in the source catalog above\n"
            "   (e.g. SRC-20260508-001). Do NOT use made-up IDs like SRC-HIS.\n"
            "2. Use table aliases (e.g. p for patient_info)\n"
            "3. Handle type mismatches with CAST()\n"
            "4. Handle NULLs for NOT NULL target fields with COALESCE()\n"
            "5. Add '-- transform: description' comments for complex logic\n"
            "6. Output ONLY valid JSON with exactly these keys:\n"
            '   "map_sql": "the complete SELECT...FROM...SQL",\n'
            '   "clean_rules": [\n'
            '     {"target_field": "table.field", "rules": ["uppercase"], "coalesce_default": ""}\n'
            '   ]\n'
        )
        self.progress_callback(
            "step2",
            f"  Prompting LLM for SQL generation ({target_schema.table}) …",
            {"prompt_preview": prompt[:500]},
        )
        raw = self.llm_call(prompt)
        self.progress_callback("step2", "  LLM raw response:", {"raw": raw})
        return _safe_json_parse(raw, default={"map_sql": "", "clean_rules": []})

    def _step4_gap_fill(
        self,
        uncovered: list[str],
        target_table: str,
        source_text: str,
    ) -> list[dict[str, Any]]:
        """Find source mappings for uncovered target fields."""
        fields_str = ", ".join(uncovered)
        prompt = (
            f"The following target fields in table '{target_table}' were not mapped "
            "in the initial SQL generation. Search ALL source tables below to find "
            "appropriate source fields.\n\n"
            f"Unmapped fields: {fields_str}\n\n"
            "=== ALL SOURCE TABLES ===\n"
            f"{source_text}\n\n"
            "Respond with JSON array. Each item:\n"
            '{"target_field": "field_name", "source_table": "src_table", '
            '"source_field": "src_col", "transform": "CAST(src_col AS DATE)", '
            '"rationale": "why"}'
        )
        self.progress_callback(
            "step4",
            f"  Prompting LLM for gap-fill ({target_table}) …",
            {"prompt_preview": prompt[:500]},
        )
        raw = self.llm_call(prompt)
        self.progress_callback("step4", "  LLM raw response:", {"raw": raw})
        data = _safe_json_parse(raw, default=[])
        return data if isinstance(data, list) else []

    @staticmethod
    def _default_llm_call(prompt: str) -> str:
        """Placeholder when no LLM is configured."""
        return json.dumps({
            "error": "No LLM configured",
            "hint": "Pass llm_call=... to DataRequirementAnalysisSkill",
        })


# ---------------------------------------------------------------------------
# Step 0: source summarisation
# ---------------------------------------------------------------------------


def _summarize_report(report: SourceExploreReport) -> str:
    """Convert an explore report into a concise LLM-friendly summary."""
    lines: list[str] = [f"=== Source: {report.source_id} ==="]
    for t in report.tables:
        lines.append(
            f"表: {t.name} "
            f"({t.comment or '无注释'}, {t.row_count:,}行)"
        )
        for c in t.columns:
            sample_str = ""
            if c.sample_data:
                samples = [str(s) for s in c.sample_data[:3]]
                sample_str = f", 样例: {', '.join(samples)}"
            null_note = ""
            if c.stats.null_rate > 0:
                null_note = f", null率{c.stats.null_rate:.1%}"
            pk_note = ", PK" if c.name in t.primary_key else ""
            lines.append(
                f"  - {c.name}: {c.data_type}"
                f"{null_note}{pk_note}{sample_str}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3: coverage audit (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _audit_coverage(sql: str, target_fields: list[str]) -> list[str]:
    """Parse SQL SELECT list and return target fields that are NOT aliased."""
    if not sql.strip():
        return list(target_fields)

    selected = _extract_select_aliases(sql)
    return [f for f in target_fields if f not in selected]


def _extract_select_aliases(sql: str) -> set[str]:
    """Extract column aliases from the outermost SELECT statement."""
    try:
        parsed = sqlparse.parse(sql)
    except Exception:
        return set()

    if not parsed:
        return set()

    stmt = parsed[0]
    aliases: set[str] = set()
    in_select = False

    for token in stmt.tokens:
        # Enter SELECT list
        if token.ttype is sqlparse.tokens.DML and token.value.upper() == "SELECT":
            in_select = True
            continue
        # Exit at FROM (outermost only — we ignore sub-queries for simplicity)
        if in_select and token.ttype is sqlparse.tokens.Keyword and token.value.upper() == "FROM":
            break
        if not in_select:
            continue

        aliases.update(_aliases_from_token(token))

    return aliases


def _aliases_from_token(token: Any) -> set[str]:
    """Recursively collect aliases from a sqlparse token."""
    aliases: set[str] = set()

    if isinstance(token, sqlparse.sql.IdentifierList):
        for ident in token.get_identifiers():
            aliases.update(_aliases_from_token(ident))
    elif isinstance(token, sqlparse.sql.Identifier):
        alias = token.get_alias()
        if alias:
            aliases.add(alias)
        else:
            real = token.get_real_name()
            if real:
                aliases.add(real)
    elif isinstance(token, sqlparse.sql.Function):
        # Functions sometimes wrap an Identifier
        for sub in token.tokens:
            aliases.update(_aliases_from_token(sub))
    return aliases


# ---------------------------------------------------------------------------
# Step 5: merge gap-fill into base SQL
# ---------------------------------------------------------------------------


def _merge_gaps_into_sql(base_sql: str, gap_fill: list[dict[str, Any]]) -> str:
    """Append gap-filled fields into the SELECT list and add any new JOINs."""
    if not gap_fill:
        return base_sql

    # Build new SELECT expressions
    new_select_lines: list[str] = []
    new_joins: list[str] = []

    for gap in gap_fill:
        src_table = gap.get("source_table", "")
        src_field = gap.get("source_field", "")
        transform = gap.get("transform", "")
        target_field = gap.get("target_field", "")

        if transform:
            expr = transform
        elif src_field:
            expr = src_field
        else:
            expr = "NULL"

        new_select_lines.append(f"    {expr} AS {target_field},")

        # If source table is not already in SQL, add a LEFT JOIN hint
        if src_table and src_table.lower() not in base_sql.lower():
            new_joins.append(
                f"-- TODO: JOIN {src_table} for field {target_field}"
            )

    if not new_select_lines:
        return base_sql

    # Insert new fields before FROM
    # Strategy: find the line with "FROM" and insert before it
    lines = base_sql.splitlines()
    from_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped.startswith("FROM ") or stripped == "FROM":
            from_idx = i
            break

    if from_idx is not None:
        # Insert before FROM
        insert_lines = new_select_lines
        if new_joins:
            insert_lines = insert_lines + [""] + [f"    {j}" for j in new_joins]
        lines = lines[:from_idx] + insert_lines + [""] + lines[from_idx:]
        return "\n".join(lines)

    # Fallback: append at end
    return base_sql + "\n" + "\n".join(new_select_lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_json_parse(raw: str, default: Any) -> Any:
    """Try to extract JSON from an LLM response (handles markdown fences, <think> blocks)."""
    text = raw.strip()

    # Strip <think>...</think> reasoning blocks (used by DeepSeek / MiniMax-M2.7)
    if text.startswith("<think>"):
        end = text.find("</think>")
        if end != -1:
            text = text[end + len("</think>"):].strip()

    # Strip markdown code fences
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find the outermost JSON object or array
    start_obj = text.find("{")
    start_arr = text.find("[")
    start = min(
        s for s in (start_obj, start_arr) if s != -1
    ) if any(s != -1 for s in (start_obj, start_arr)) else -1

    if start != -1:
        # Find the matching closing bracket
        if start == start_arr:
            end = text.rfind("]")
        else:
            end = text.rfind("}")
        if end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

    return default


def _avg_scope_confidence(scope: dict[str, Any]) -> float:
    """Average confidence across all target tables in the scope."""
    if not scope:
        return 0.0
    scores = []
    for v in scope.values():
        if isinstance(v, dict):
            scores.append(v.get("confidence", 0.0))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _derive_field_mappings(
    sql: str, target_table: str, table_scope: dict[str, Any]
) -> list[FieldMapping]:
    """Best-effort derivation of FieldMapping list from generated SQL.

    Used for audit trail; may be incomplete for complex expressions.
    """
    mappings: list[FieldMapping] = []
    primary = table_scope.get("primary_source", {}).get("table", "")

    # Simple regex-based extraction of "expr AS alias"
    import re

    pattern = re.compile(r"([\w\.\(\),\s_'%+\-]+?)\s+AS\s+(\w+)", re.IGNORECASE)
    for match in pattern.finditer(sql):
        expr = match.group(1).strip()
        alias = match.group(2).strip()

        # Heuristic: if expr is a simple column name, it's direct
        if re.match(r"^[\w_]+$", expr):
            mapping_type = "direct"
        elif "CAST(" in expr.upper() or "COALESCE(" in expr.upper():
            mapping_type = "transform"
        else:
            mapping_type = "transform"

        mappings.append(
            FieldMapping(
                target_field=f"{target_table}.{alias}",
                source_field=expr if mapping_type == "direct" else None,
                source_table=primary or None,
                mapping_type=mapping_type,  # type: ignore[arg-type]
                transform_logic=expr if mapping_type == "transform" else "",
            )
        )

    return mappings


# ---------------------------------------------------------------------------
# Optional OpenAI helper (zero extra deps — stdlib only)
# ---------------------------------------------------------------------------


def create_openai_llm_call(
    model: str = "gpt-4o",
    api_key: str = "",
    base_url: str = "https://api.openai.com/v1",
) -> Callable[[str], str]:
    """Return an *llm_call* backed by the OpenAI Chat Completions API.

    Uses only stdlib (``urllib``) so no extra package is required.

    .. deprecated::
        Use :func:`antline.core.llm_config.create_llm_call` with an
        :class:`~antline.core.llm_config.LLMConfig` instead.
    """
    from antline.core.llm_config import LLMConfig, create_llm_call

    config = LLMConfig(
        provider="openai",
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    return create_llm_call(config)
