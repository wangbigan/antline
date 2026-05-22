# Antline

> CLI data production management tool — from source exploration to delivery.

Antline brings project management discipline to data engineering.
It provides a structured CLI workflow for data teams (and agents) to:

1. **Explore** data sources — metadata, statistics, sample data
2. **Define** data requirements — target schema from CSV or YAML
3. **Assess** feasibility — LLM-driven intelligent analysis with "generate → audit → patch" feedback loop, producing model-level SQL directly
4. **Build** data pipelines — dbt-native scaffolding (row / map / clean)
5. **Validate** data quality — dbt tests + custom checks
6. **Deliver** production data — versioned, auditable, reproducible

## Why Antline?

- **Agent-first**: Structured CLI output designed for LLM/Agent consumption (`--json` on every command)
- **Human-friendly**: Interactive prompts and rich reports for manual workflows
- **Git-native**: All state stored as YAML files — version control your data projects
- **Workspace-centric**: One workspace = one data platform, all projects share the same target database
- **Security-first**: No credentials stored in any config file; all passwords prompted at runtime with audit logging
- **Lightweight**: Delegates execution to dbt; Antline manages the workflow layer
- **Open source**: Apache-2.0, built for independent developers and small teams

## Quick Start

```bash
# Install
pip install antline[all]

# Initialize a workspace (target database platform config)
mkdir my-data-workspace && cd my-data-workspace
antline init --name "Hospital Data Team" \
  --db-type postgresql --host localhost --port 5432

# Add a data source (password prompted at runtime)
antline source add --type postgresql --host localhost --port 5432 \
  --database mydb --user myuser

# Explore the source
antline source explore SRC-20260508-001

# Import target schema from CSV (e.g. MIMIC-IV standard)
antline schema import /path/to/standard_schema.csv --output-dir target_schema

# Define a requirement
antline requirement create --name "Unified patient view" \
  --background "Hospital needs a unified patient dimension" \
  --goal "Build a standardized patients table from HIS + EMR"

# Add target schema to the requirement (YAML file, directory, or CSV)
antline requirement add-schema REQ-20260508-001 target_schema/patients.yaml
antline requirement add-schema REQ-20260508-001 target_schema/hosp/
antline requirement add-schema REQ-20260508-001 standard_schema.csv

# Assess feasibility (two modes)

# Option A: Auto-analysis with LLM (recommended for agents)
# Runs a 5-step pipeline: table scope → SQL generation → coverage audit → gap-fill → merge
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto

# Option B: Manual review (generates prompt.md + guide.md + template.md)
antline requirement assess REQ-20260508-001 SRC-20260508-001
# After reviewing assessment materials and saving as assessment.md:
antline requirement approve REQ-20260508-001

# Create project and scaffold pipeline
antline project create --name "Patient 360" --requirement REQ-20260508-001

# Scaffold (credentials prompted at runtime, never stored)
antline project scaffold PRJ-20260508-001 --user myuser --password '***'

# Compile (validate SQL syntax without executing)
antline project compile PRJ-20260508-001
antline project compile PRJ-20260508-001 -m map_patients

# Build with dbt (credentials prompted at runtime)
antline project build PRJ-20260508-001

# Validate and deliver (credentials prompted at runtime)
antline project validate PRJ-20260508-001
antline project deliver PRJ-20260508-001
```

## Installation

```bash
# Basic install (PostgreSQL only)
pip install antline[postgres]

# With MySQL/TiDB support
pip install antline[mysql,tidb]

# All database drivers + dev tools
pip install antline[all,dev]
```

**Requirements:**
- Python 3.10+
- Git (for version control)
- dbt (for SQL execution, install separately: `pip install dbt-core dbt-postgres`)
- PostgreSQL (for target database, if using FDW mode)

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Source    │────▶│ Requirement │────▶│   Project   │
│  Management │     │  Management │     │  Management │
└─────────────┘     └─────────────┘     └─────────────┘
                                                │
                                                ▼
                                        ┌─────────────┐
                                        │  dbt / SQL  │
                                        │  Execution  │
└───────────────────────────────────────────────────────┘
│         Workspace Platform (shared database)          │
└───────────────────────────────────────────────────────┘
```

| Layer | Technology | Role |
|-------|------------|------|
| CLI | Typer + Rich | User interface (human + agent) |
| State | Git-native YAML | Zero-database state management |
| Models | Pydantic | Type-safe data entities |
| DB | SQLAlchemy | Multi-database metadata reflection |
| Execution | External dbt | SQL transformation engine |
| Platform | Workspace-level | Shared target database config |

## Workspace Structure

```
my-workspace/
├── antline.yml              # Workspace config + platform
├── .gitignore               # Excludes passwords, generated reports
├── sources/
│   └── SRC-20260508-001/
│       ├── source.yml       # Data source configuration
│       └── explore/
│           ├── report.yml   # Structured report (for agents)
│           └── report.md    # Human-readable report
├── requirements/
│   └── REQ-20260508-001/
│       ├── requirement.yml  # Requirement definition
│       ├── target_schema/   # Target data standard YAMLs
│       └── assessment/
│           ├── prompt.md    # LLM prompt
│           ├── guide.md     # Human guide
│           ├── template.md  # Empty template
│           └── assessment.md # Completed assessment
├── projects/
│   └── PRJ-20260508-001/
│       ├── project.yml      # Project definition
│       ├── dbt/             # Per-project dbt directory
│       │   ├── dbt_project.yml
│       │   ├── profiles.yml
│       │   └── models/
│       │       ├── row/     # Row layer
│       │       ├── map/     # Map layer
│       │       ├── clean/   # Clean layer
│       │       └── sources.yml
│       └── qc/
│           └── report.md    # QC report
└── reports/                 # Workspace-level reports
```

### ID Format

All entities use date-based IDs:
- `SRC-YYYYMMDD-NNN` — e.g. `SRC-20260508-001`
- `REQ-YYYYMMDD-NNN` — e.g. `REQ-20260508-001`
- `PRJ-YYYYMMDD-NNN` — e.g. `PRJ-20260508-001`

IDs are sequential within the same date. Cross-date IDs do not interfere with each other.

## Command Reference

### Global

| Command | Description |
|---------|-------------|
| `antline --version` | Show version |
| `antline init [--path DIR] [--name NAME] --db-type TYPE --host H --port P [--user U] [--password PWD] [--no-test-connection]` | Initialize workspace with platform config (tests connection, credentials not stored) |
| `antline status` | Show workspace overview (sources, requirements, projects) |

### Source Management

| Command | Description |
|---------|-------------|
| `antline source add --type {postgresql\|mysql\|tidb} ...` | Add a data source (validates connection) |
| `antline source list [--json]` | List all sources |
| `antline source explore SRC-xxx [--max-tables N] [--no-mask]` | Explore metadata + statistics (generates `explore/report.yml` + `report.md`) |
| `antline source show SRC-xxx` | Show source details |
| `antline source update SRC-xxx --host newhost ...` | Update source fields |
| `antline source remove SRC-xxx [--force]` | Remove a source |

### Schema Management

| Command | Description |
|---------|-------------|
| `antline schema import CSV_FILE [--output-dir DIR]` | Import target schema from CSV |
| `antline schema list` | List imported schemas |
| `antline schema show TABLE_NAME` | Show schema definition |

### Requirement Management

| Command | Description |
|---------|-------------|
| `antline requirement create --name NAME [--background TEXT] [--goal TEXT]` | Create a requirement |
| `antline requirement list [--json]` | List all requirements |
| `antline requirement show REQ-xxx` | Show requirement details |
| `antline requirement add-schema REQ-xxx PATH [PATH ...]` | Add target schema YAML(s), directory, or CSV to a requirement |
| `antline requirement assess REQ-xxx SRC-xxx [SRC-yyy ...] [--focus TABLES] [--full] [--auto] [--step {scope\|generate}] [--scope-file PATH] [--json] [--min-confidence N]` | Generate assessment. Default: prompt.md + guide.md + template.md. `--auto`: LLM-driven 5-step analysis producing model SQL + clean rules |
| `antline requirement approve REQ-xxx [--file PATH] [--force] [--note TEXT]` | Confirm requirement after reviewing assessment.md. Validates source_table/field references against explore reports. Use `--force` to bypass validation or re-approve an IN_PROJECT requirement (requires `--note`). |
| `antline requirement update REQ-xxx ...` | Update requirement (resets assessment) |
| `antline requirement remove REQ-xxx [--force]` | Remove a requirement |

### Project Management

| Command | Description |
|---------|-------------|
| `antline project create --name NAME --requirements REQ-xxx` | Create project from approved requirements |
| `antline project list [--json]` | List all projects |
| `antline project show PRJ-xxx` | Show project details |
| `antline project scaffold PRJ-xxx [--source-mode {fdw\|sync}] [--skip-db-setup] [--user U] [--password PWD]` | Generate dbt project scaffolding (credentials prompted if not provided) |
| `antline project compile PRJ-xxx [-m MODEL] [--user U] [--password PWD]` | Validate SQL syntax without executing |
| `antline project build PRJ-xxx [--user U] [--password PWD]` | Build with dbt |
| `antline project validate PRJ-xxx [--user U] [--password PWD]` | Run data quality tests |
| `antline project deliver PRJ-xxx` | Mark as production-ready |

## Scaffold: Row Layer Source Modes

When scaffolding a project, row layer models can reference source tables in two ways:

### FDW Mode (default)

Uses PostgreSQL Foreign Data Wrapper to query external databases as foreign tables.

```bash
antline project scaffold PRJ-20260508-001 --source-mode fdw
```

Prerequisites:
1. Run the auto-generated FDW setup script before `dbt build`:
   ```bash
   psql -d antline_workspace -f projects/PRJ-20260508-001/dbt/sql/fdw_setup.sql
   ```
2. This creates foreign tables in schemas named after the source databases (e.g. `his_db.patients`)

### Sync Mode

Expects data to be physically synced into the target database's ODS layer first.

```bash
antline project scaffold PRJ-20260508-001 --source-mode sync
```

Prerequisites:
1. Run extract job to copy source data into target DB's `ods_src_001` schema
2. Then `dbt build` queries local ODS tables

## Workflow Example: Hospital Data Integration

### 1. Initialize Workspace

```bash
antline init --name "医院数据团队" \
  --db-type postgresql --host localhost --port 5432
```

### 2. Define Target Standard

Create a CSV file with your target data standard:

```csv
module,table_name,table_comment,field_name,field_type,field_comment,example
Hosp,patients,患者信息,subject_id,INTEGER NOT NULL,患者唯一标识符,10000032
Hosp,patients,患者信息,gender,VARCHAR(1),性别,F; M
Hosp,patients,患者信息,age,INTEGER,年龄,65
```

Import it:

```bash
antline schema import hospital_standard.csv --output-dir target_schema
```

### 3. Explore Source Databases

```bash
# Add HIS system database (connection is validated before saving, password prompted)
antline source add --type postgresql --host db.hospital.local \
  --database his_db --user wbg

# Explore structure (generates both report.yml for agents and report.md for humans)
# Password is prompted at runtime and never stored
# Sample data in sensitive fields is masked by default
antline source explore SRC-20260508-001

# Or disable masking when you need raw values
antline source explore SRC-20260508-001 --no-mask
```

Output:
```
Database: his_db (postgresql)
Tables: 47 | Rows: 1,234,567 | Columns: 892

┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Table              ┃ Schema ┃ Rows     ┃ Columns ┃ PK      ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ patient_visits     │ public │ 500,000  │ 12      │ visit_id│
│ patient_info       │ public │ 120,000  │ 8       │ id      │
│ ...                │ ...    │ ...      │ ...     │ ...     │
└────────────────────┴────────┴──────────┴─────────┴─────────┘
```

### 4. Create, Assess, and Approve Requirements

```bash
# 1. Create the requirement (background + goal)
antline requirement create --name "统一患者视图" \
  --background "医院 HIS 和 EMR 系统患者数据分散，需要统一视图" \
  --goal "建立 MIMIC-IV 标准的 patients + admissions 维度表"

# 2. Add target schema(s) to the requirement (YAML, directory, or CSV)
antline requirement add-schema REQ-20260508-001 target_schema/patients.yaml
antline requirement add-schema REQ-20260508-001 target_schema/hosp/
antline requirement add-schema REQ-20260508-001 hospital_standard.csv

# 3. Assess feasibility — two modes available:

# Mode A: Auto-analysis with LLM (recommended for agents)
# Runs 5-step pipeline: table scope → SQL generation → coverage audit → gap-fill → merge
# Produces model-level SQL + clean rules directly, no manual template filling needed
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto

# Step-by-step: only analyze table scope (Step 1), output JSON
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step scope --json

# Step-by-step: generate SQL from an existing scope file (Steps 2-5)
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step generate --scope-file scope.json

# Mode B: Manual review (generates prompt.md + guide.md + template.md)
antline requirement assess REQ-20260508-001 SRC-20260508-001

# Focus on specific tables only
antline requirement assess REQ-20260508-001 SRC-20260508-001 --focus patient_info,admission_records

# Include full field statistics (null rates, unique counts, top values)
antline requirement assess REQ-20260508-001 SRC-20260508-001 --full

# 4. Approve
# For auto-assessment: approve directly (assessment already in requirement.yml)
antline requirement approve REQ-20260508-001

# For manual assessment: review materials, save as assessment.md, then approve
# Approval validates source_table/source_field against explore reports
antline requirement approve REQ-20260508-001

# If validation fails (e.g. table/field doesn't exist in explore report),
# fix assessment.md or use --force to bypass
antline requirement approve REQ-20260508-001 --force
```

**Design note:** `assess` supports two modes:

| Mode | Flag | Output | Best for |
|------|------|--------|----------|
| Auto (LLM) | `--auto` | `model_sqls` + `clean_rules` + `field_mappings` + `scope.json` | Agents / automated pipelines |
| Manual | (default) | `prompt.md` + `guide.md` + `template.md` | Human review / external LLM |

| File | Purpose |
|------|---------|
| `prompt.md` | LLM prompt with target schema + source metadata |
| `guide.md` | Human-readable assessment guide |
| `template.md` | Empty Markdown template with YAML frontmatter |

Copy the prompt to an LLM, review the output, save it as `assessment.md`,
then run `approve` to store the assessment in the requirement.

**Auto-assessment 5-step pipeline** (`--auto`):

```
Step 0: Context Preparation    → Summarise explore reports into LLM-friendly text
Step 1: Table Scope Analysis   → Which source tables feed each target table (with JOIN relations)
Step 2: Model SQL Generation   → Full dbt model SQL for each target table
Step 3: Coverage Audit         → AST parse SQL, diff against target schema (deterministic, no LLM)
Step 4: Gap-fill Search        → Find mappings for uncovered fields across ALL source tables
Step 5: Model Merge            → Incrementally patch gaps back into the SQL
```

Benefits:
- **Token-efficient**: Step 1 uses wide context (all tables), Steps 2-5 use narrow context (scoped tables only)
- **High coverage**: Audit + gap-fill ensures no target field is left unmapped
- **Model-level SQL**: Output is a complete `SELECT ... FROM ... JOIN ...` statement, not field-by-field mappings
- **Clean rules**: Automatically generates `clean_rules` (CAST, COALESCE, TRIM, UPPER, etc.) for the clean layer
- **Audit trail**: Produces `scope.json` + per-model `.sql` files for human review

**Approval validation:** During `approve`, Antline cross-checks every
non-`missing` mapping against the source explore reports:
- `source_table` must exist in the corresponding source's explore report
- `source_field` must exist in that table's columns

If validation fails, errors are printed with line numbers. Use `--force` to
approve anyway (e.g. for planned future schema changes).

**Re-approval:** If a requirement is already in a project (`IN_PROJECT` status),
you can re-approve it with `--force` and a `--note` explaining the reason:
```bash
antline requirement approve REQ-20260508-001 --force --note "修正表名: visits -> inpatient_visits"
```
The status remains `IN_PROJECT`, and the note is recorded in the assessment.

Assessment output:
```
评估材料已生成:
  LLM 提示词:  requirements/REQ-20260508-001/assessment/prompt.md
  人工指南:    requirements/REQ-20260508-001/assessment/guide.md
  评估模板:    requirements/REQ-20260508-001/assessment/template.md

下一步操作:
  1. 将 prompt.md 的内容复制给大模型，获取评估结果
  2. 人工审核修改后，保存为 assessment.md
  3. 审批通过: antline requirement approve REQ-20260508-001
```

### 5. Create Project and Scaffold

```bash
antline project create --name "患者数据集成项目" --requirement REQ-20260508-001

# Scaffold (credentials prompted at runtime, never stored)
antline project scaffold PRJ-20260508-001

# Setup FDW (for fdw mode)
psql -d hospital_data -f projects/PRJ-20260508-001/dbt/sql/fdw_setup.sql
```

Generated dbt models (with `--auto`, model-level SQL):

```sql
-- projects/PRJ-20260508-001/dbt/models/map/map_patients.sql
-- Map layer: patients
-- Requirement: REQ-20260508-001

SELECT
    p.patient_id AS subject_id,
    p.gender AS gender,
    CAST(p.age AS INTEGER) AS anchor_age,
    EXTRACT(YEAR FROM p.birthday) AS anchor_year,
    -- transform: age group categorization
    CASE
        WHEN p.age < 18 THEN 'pediatric'
        WHEN p.age < 65 THEN 'adult'
        ELSE 'elderly'
    END AS anchor_year_group,
    v.dod AS dod
FROM {{ source('SRC-001', 'patient_info') }} p
LEFT JOIN {{ source('SRC-001', 'visits') }} v
    ON p.patient_id = v.patient_id
```

Clean layer (with `clean_rules`):

```sql
-- projects/PRJ-20260508-001/dbt/models/clean/clean_patients.sql
-- Clean layer: patients

SELECT
    patient_id,
    UPPER(COALESCE(TRIM(gender), 'U')) AS gender,
    CAST(anchor_age AS INTEGER) AS anchor_age,
    CAST(anchor_year AS INTEGER) AS anchor_year,
    anchor_year_group,
    dod
FROM {{ ref('map_patients') }}
```

### 6. Compile, Build and Validate

```bash
# Validate SQL syntax without executing (fast, credentials prompted)
antline project compile PRJ-20260508-001

# Build with dbt (credentials prompted)
antline project build PRJ-20260508-001

# Validate and deliver (credentials prompted)
antline project validate PRJ-20260508-001
antline project deliver PRJ-20260508-001
```

## Agent API Guide

Antline is designed for LLM/Agent consumption. Every command supports `--json`:

```bash
# List sources as JSON
antline source list --json

# List requirements as JSON
antline requirement list --json

# Explore and output raw YAML
antline source explore SRC-20260508-001 --json
```

**Agent workflow pattern:**

```python
# 1. Agent initializes workspace (no credentials stored)
run("antline init --name X --db-type postgresql --host localhost --port 5432")

# 2. Agent reads source metadata (password prompted at runtime)
report = yaml.safe_load(run("antline source explore SRC-20260508-001 --json"))

# 3. Agent defines requirement with background + goal
run(
    "antline requirement create --name X "
    "--background '...' --goal '...'"
)
run("antline requirement add-schema REQ-20260508-001 schema.yaml")

# 4. Agent runs auto-assessment (5-step LLM pipeline)
#    Produces model_sqls + clean_rules + field_mappings + scope.json
result = json.loads(run(
    "antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --json"
))

# 5. Agent reviews uncovered fields (if any) and decides whether to approve
if result["approval_recommendation"] == "auto" and not result["uncovered_fields"]:
    run("antline requirement approve REQ-20260508-001")
else:
    # Review low-confidence mappings or uncovered fields
    for f in result["uncovered_fields"]:
        print(f"Uncovered: {f}")
    # Agent can fix individual mappings, or use --force to approve anyway
    run("antline requirement approve REQ-20260508-001 --force")

# Alternative: step-by-step for fine-grained control
# Step 1: table scope only
run("antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step scope")
# Agent reviews scope.json, then Step 2-5: generate SQL from scope
run("antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step generate --scope-file scope.json")

# 7. Create project and scaffold
run("antline project create --name X --requirements REQ-20260508-001")
run("antline project scaffold PRJ-20260508-001 --source-mode fdw")

# 8. Compile and build (credentials prompted at runtime)
run("antline project compile PRJ-20260508-001")
run("antline project build PRJ-20260508-001")
```

## CSV Schema Format

Antline accepts CSV files with these columns:

| Column | Description |
|--------|-------------|
| `module` | Business module name (e.g. `Hosp`, `ICU`) |
| `table_name` | Target table name |
| `table_comment` | Table description |
| `field_name` | Field/column name |
| `field_type` | Data type with optional `NOT NULL` (e.g. `INTEGER NOT NULL`) |
| `field_comment` | Field description |
| `example` | Example values |

## Development

```bash
# Clone
git clone https://github.com/wangbigan/antline.git
cd antline

# Install in editable mode
pip install -e ".[all,dev]"

# Run tests
pytest tests/ -v

# Format and lint
ruff format antline/ tests/
ruff check antline/ tests/
```

## Roadmap

- [x] Source management (add, list, explore)
- [x] Schema import from CSV
- [x] Requirement management (create, assess, update)
- [x] Project management (create, scaffold)
- [x] Per-project dbt directories (`projects/PRJ-xxx/dbt/`)
- [x] Workspace-level platform configuration
- [x] Date-based entity IDs (SRC/REQ/PRJ-YYYYMMDD-NNN)
- [x] Subdirectory structure for sources/requirements/projects
- [x] Interactive scaffold with database setup prompts
- [x] FDW / sync dual source modes for row layer
- [x] SQL compile command (validate without executing)
- [x] dbt integration (build, validate)
- [x] Connection validation for source add
- [x] PII-aware data masking in explore reports
- [x] Dual-format reports (YAML for agents + Markdown for humans)
- [x] No credential storage — all passwords prompted at runtime
- [x] Audit logging for compliance
- [x] Approval validation against explore reports
- [x] Re-approval for IN_PROJECT requirements with notes
- [x] Extract job (physical data sync for sync mode)
- [x] **Intelligent requirement assessment** (`--auto`): LLM-driven 5-step pipeline (scope → SQL → audit → gap-fill → merge), produces model-level SQL + clean rules directly
- [ ] Schedule command (cron wrapper / Airflow DAG generation)
- [ ] Plugin system for custom ETL backends
- [ ] Web UI (lightweight)

## License

Apache-2.0
