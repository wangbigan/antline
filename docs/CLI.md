# CLI Reference

Complete reference for all Antline CLI commands.

---

## Global Commands

### `antline init`

Initialize a new Antline workspace.

```bash
antline init \
  --path DIR \
  --name NAME \
  --db-type {postgresql|mysql|tidb} \
  --host HOST \
  --port PORT \
  [--user USER] \
  [--password PWD] \
  [--no-test-connection] \
  [--force]
```

| Option | Description |
|--------|-------------|
| `--path` | Workspace directory (default: current directory) |
| `--name` | Workspace name |
| `--db-type` | Target database type |
| `--host` | Database host |
| `--port` | Database port |
| `--user` | Database user (prompted if not provided and `--no-test-connection` is not set) |
| `--password` | Database password (prompted if not provided) |
| `--no-test-connection` | Skip connection test |
| `--force` | Overwrite existing workspace |

**Example:**
```bash
antline init --name "Hospital Data Team" \
  --db-type postgresql --host localhost --port 5432
```

### `antline status`

Show workspace overview (sources, requirements, projects).

```bash
antline status
```

---

## Source Management

### `antline source add`

Add a data source. Connection is validated before saving; password is prompted at runtime and never stored.

```bash
antline source add \
  --type {postgresql|mysql|tidb} \
  --host HOST \
  --port PORT \
  --database DB \
  --user USER \
  [--password PWD] \
  [--no-test-connection]
```

| Option | Description |
|--------|-------------|
| `--type` | Database type |
| `--host` | Database host |
| `--port` | Database port |
| `--database` | Database name |
| `--user` | Database user |
| `--password` | Database password (prompted if not provided) |
| `--no-test-connection` | Skip connection validation |

**Example:**
```bash
antline source add --type postgresql --host db.hospital.local \
  --database his_db --user wbg
```

### `antline source list`

```bash
antline source list [--json]
```

### `antline source show`

```bash
antline source show SRC-xxx
```

### `antline source explore`

Explore source metadata, statistics, and sample data.

```bash
antline source explore SRC-xxx \
  [--max-tables N] \
  [--no-mask] \
  [--json]
```

| Option | Description |
|--------|-------------|
| `--max-tables` | Limit number of tables to explore |
| `--no-mask` | Disable PII masking in sample data |
| `--json` | Output raw report data as JSON |

### `antline source update`

```bash
antline source update SRC-xxx \
  [--name NAME] \
  [--host HOST] \
  [--port PORT] \
  [--database DB] \
  [--user USER]
```

### `antline source remove`

```bash
antline source remove SRC-xxx [--force]
```

---

## Schema Management

### `antline schema import`

Import target schema from CSV.

```bash
antline schema import CSV_FILE [--output-dir DIR]
```

### `antline schema list`

```bash
antline schema list
```

### `antline schema show`

```bash
antline schema show TABLE_NAME
```

---

## Requirement Management

### `antline requirement create`

```bash
antline requirement create \
  --name NAME \
  [--background TEXT] \
  [--goal TEXT] \
  [--target-schema PATH ...] \
  [--id CUSTOM_ID]
```

### `antline requirement list`

```bash
antline requirement list [--json]
```

### `antline requirement show`

```bash
antline requirement show REQ-xxx
```

### `antline requirement add-schema`

Add target schema(s) to a requirement. Supports YAML files, directories, or CSV.

```bash
antline requirement add-schema REQ-xxx PATH [PATH ...]
```

**Examples:**
```bash
antline requirement add-schema REQ-001 target_schema/patients.yaml
antline requirement add-schema REQ-001 target_schema/hosp/
antline requirement add-schema REQ-001 standard_schema.csv
```

### `antline requirement assess`

Generate assessment. Two modes available:

#### Default mode (manual review)

Generates `prompt.md` + `guide.md` + `template.md` for human/LLM review.

```bash
antline requirement assess REQ-xxx SRC-xxx [SRC-yyy ...] \
  [--focus TABLES] \
  [--full]
```

| Option | Description |
|--------|-------------|
| `--focus` | Comma-separated list of source tables to focus on |
| `--full` | Include full field statistics (null rates, unique counts, top values) |

#### Auto mode (LLM-driven analysis)

Runs a 5-step pipeline producing model-level SQL + clean rules directly.

```bash
antline requirement assess REQ-xxx SRC-xxx [SRC-yyy ...] --auto \
  [--step {scope|generate}] \
  [--scope-file PATH] \
  [--json] \
  [--min-confidence N]
```

| Option | Description |
|--------|-------------|
| `--auto` | Enable LLM-driven automatic analysis |
| `--step` | Run only a specific step (`scope` or `generate`) |
| `--scope-file` | Provide a pre-generated scope JSON file (for `--step generate`) |
| `--json` | Output structured JSON instead of human-readable text |
| `--min-confidence` | Only show mappings with confidence >= N |

**Examples:**
```bash
# Full auto pipeline
antline requirement assess REQ-001 SRC-001 --auto

# Step 1 only: table scope analysis
antline requirement assess REQ-001 SRC-001 --auto --step scope --json

# Steps 2-5: generate SQL from existing scope
antline requirement assess REQ-001 SRC-001 --auto --step generate --scope-file scope.json

# Filter low-confidence mappings
antline requirement assess REQ-001 SRC-001 --auto --json --min-confidence 0.7
```

**Auto mode 5-step pipeline:**

| Step | Name | Input | Output | Uses LLM? |
|------|------|-------|--------|-----------|
| 0 | Context Preparation | Explore reports | Source summaries | No |
| 1 | Table Scope Analysis | Target schemas + all sources | `{target_table: {primary_source, join_sources, confidence}}` | Yes |
| 2 | Model SQL Generation | Single target + scoped sources | Full dbt model SQL + clean_rules | Yes |
| 3 | Coverage Audit | Generated SQL + target schema | Uncovered fields list | No (AST parse) |
| 4 | Gap-fill Search | Uncovered fields + all sources | Supplementary mappings | Yes |
| 5 | Model Merge | Base SQL + gap-fill results | Final patched SQL | No |

**Auto mode output:**
- `requirements/REQ-xxx/assessment/scope.json` — Table scope analysis
- `requirements/REQ-xxx/assessment/{model_name}.sql` — Generated dbt model SQL
- Stored in `requirement.yml` under `assessment.model_sqls`, `assessment.clean_rules`, `assessment.field_mappings`

### `antline requirement approve`

Approve a requirement after review.

```bash
antline requirement approve REQ-xxx \
  [--file PATH] \
  [--force] \
  [--note TEXT]
```

| Option | Description |
|--------|-------------|
| `--file` | Path to assessment.md (default: `requirements/REQ-xxx/assessment/assessment.md`) |
| `--force` | Bypass validation or re-approve an IN_PROJECT requirement |
| `--note` | Re-approval reason (required with `--force` for IN_PROJECT requirements) |

**Behavior:**
- If the requirement has an auto-assessment (`auto_assessed: true`), approval uses the stored assessment directly (no `assessment.md` needed).
- Otherwise, reads `assessment.md` and parses YAML frontmatter.
- Validates all non-`missing` mappings against explore reports.

**Examples:**
```bash
# Approve auto-assessment
antline requirement approve REQ-001

# Approve manual assessment
antline requirement approve REQ-001 --file assessment.md

# Re-approve with force (for IN_PROJECT requirements)
antline requirement approve REQ-001 --force --note "修正表名映射"
```

### `antline requirement update`

```bash
antline requirement update REQ-xxx \
  [--name NAME] \
  [--background TEXT] \
  [--goal TEXT] \
  [--target-schema PATH ...]
```

**Note:** Updating target schemas resets the assessment to `DRAFT`.

### `antline requirement remove`

```bash
antline requirement remove REQ-xxx [--force]
```

---

## Project Management

### `antline project create`

```bash
antline project create \
  --name NAME \
  --requirement REQ-xxx [REQ-yyy ...] \
  [--description TEXT] \
  [--id CUSTOM_ID]
```

**Note:** All requirements must be in `APPROVED` or `IN_PROJECT` status.

### `antline project list`

```bash
antline project list [--json]
```

### `antline project show`

```bash
antline project show PRJ-xxx
```

### `antline project scaffold`

Generate dbt project scaffolding from approved requirement assessments.

```bash
antline project scaffold PRJ-xxx \
  [--source-mode {fdw|sync}] \
  [--skip-db-setup] \
  [--user USER] \
  [--password PWD]
```

| Option | Description |
|--------|-------------|
| `--source-mode` | Row layer reference mode: `fdw` (default, foreign data wrapper) or `sync` (physical sync) |
| `--skip-db-setup` | Skip database creation and connection validation (useful for tests) |
| `--user` | Database user |
| `--password` | Database password |

**Map layer generation:**
- If assessment contains `model_sqls` (from `--auto`), writes the full SQL directly to `models/map/{model}.sql`
- Otherwise, falls back to field-by-field scaffolding from `field_mappings`

**Clean layer generation:**
- If assessment contains `clean_rules`, applies them (CAST, COALESCE, TRIM, UPPER, LOWER, etc.)
- Otherwise, generates a template with TODO comments

### `antline project compile`

Validate SQL syntax without executing.

```bash
antline project compile PRJ-xxx \
  [--model MODEL] \
  [--user USER] \
  [--password PWD]
```

### `antline project build`

Build the project with dbt.

```bash
antline project build PRJ-xxx \
  [--version TAG] \
  [--user USER] \
  [--password PWD]
```

### `antline project validate`

Run data quality tests.

```bash
antline project validate PRJ-xxx \
  [--user USER] \
  [--password PWD]
```

### `antline project deliver`

Mark project as production-ready. Requires `QC_PASSED` status.

```bash
antline project deliver PRJ-xxx
```

### `antline project extract`

Extract source tables into target database ODS layer (for sync mode).

```bash
antline project extract PRJ-xxx \
  [--source SRC-xxx] \
  [--batch-size N] \
  [--target-user USER] \
  [--target-password PWD]
```

| Option | Description |
|--------|-------------|
| `--source` | Extract only a specific source (default: all sources used by the project) |
| `--batch-size` | Rows per batch (default: 10000) |
| `--target-user` | Target database user |
| `--target-password` | Target database password |

---

## LLM Configuration

`antline requirement assess --auto` requires an LLM. Configure it in `antline.yml`:

```yaml
llm:
  provider: openai        # openai | anthropic
  model: gpt-4o           # model name
  api_key: ""             # optional; falls back to env var
  base_url: ""            # optional; for custom/proxy endpoints
  temperature: 0.2        # optional; default 0.2
```

**Environment variables** (used when `api_key` is empty):

| Provider | Variable |
|----------|----------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |

**Examples:**

```yaml
# OpenAI (default)
llm:
  provider: openai
  model: gpt-4o

# Anthropic Claude
llm:
  provider: anthropic
  model: claude-sonnet-4-6

# Custom OpenAI-compatible endpoint
llm:
  provider: openai
  model: deepseek-chat
  base_url: https://api.deepseek.com/v1
```

---

## JSON Output

Every `list` and `show` command supports `--json` for programmatic consumption:

```bash
antline source list --json
antline requirement list --json
antline requirement show REQ-001 --json
antline project list --json
antline project show PRJ-001 --json
```

The `--auto --json` combination on `assess` outputs structured analysis results:

```bash
antline requirement assess REQ-001 SRC-001 --auto --json
```

Output schema:
```json
{
  "source_scope": { "patients": { "primary_source": "...", "confidence": 0.95 } },
  "model_sqls": { "patients": "SELECT ..." },
  "field_mappings": [...],
  "clean_rules": [...],
  "uncovered_fields": [],
  "confidence": 0.95,
  "approval_recommendation": "auto"
}
```
