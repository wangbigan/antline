# Antline

> CLI data production management tool — from source exploration to delivery.

Antline brings project management discipline to data engineering.
It provides a structured CLI workflow for data teams (and agents) to:

1. **Explore** data sources — metadata, statistics, sample data
2. **Define** data requirements — target schema from CSV or YAML
3. **Assess** feasibility — automatic field mapping + risk analysis
4. **Build** data pipelines — dbt-native scaffolding (row / map / clean)
5. **Validate** data quality — dbt tests + custom checks
6. **Deliver** production data — versioned, auditable, reproducible

## Why Antline?

- **Agent-first**: Structured CLI output designed for LLM/Agent consumption (`--json` on every command)
- **Human-friendly**: Interactive prompts and rich reports for manual workflows
- **Git-native**: All state stored as YAML files — version control your data projects
- **Workspace-centric**: One workspace = one data platform, all projects share the same target database
- **Lightweight**: Delegates execution to dbt; Antline manages the workflow layer
- **Open source**: Apache-2.0, built for independent developers and small teams

## Quick Start

```bash
# Install
pip install antline[all]

# Initialize a workspace (with target database platform)
mkdir my-data-workspace && cd my-data-workspace
antline init --name "Hospital Data Team" \
  --db-type postgresql --host localhost --port 5432 \
  --user postgres --password '***' --database antline_workspace

# Add a data source
antline source add --type postgresql --host localhost --port 5432 \
  --database mydb --user myuser

# Explore the source
antline source explore SRC-20260508-001

# Import target schema from CSV (e.g. MIMIC-IV standard)
antline schema import /path/to/standard_schema.csv --output-dir target_schema

# Define a requirement (single table)
antline requirement create --name "Unified patient view" \
  --target-schema target_schema/patients.yaml \
  --background "Hospital needs a unified patient dimension" \
  --goal "Build a standardized patients table from HIS + EMR"

# Or multiple tables / a whole directory
antline requirement create --name "Inpatient full view" \
  --target-schema target_schema/hosp/ \
  --background "..." --goal "..."

# Assess feasibility against source data (draft for review)
antline requirement assess REQ-20260508-001 SRC-20260508-001

# Create project and scaffold pipeline
antline project create --name "Patient 360" --requirement REQ-20260508-001

# Scaffold (uses workspace platform config, no database params needed)
antline project scaffold PRJ-20260508-001

# Compile (validate SQL syntax without executing)
antline project compile PRJ-20260508-001
antline project compile PRJ-20260508-001 -m map_patients

# Build with dbt
cd projects/PRJ-20260508-001/dbt && dbt build

# Validate and deliver
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
│       ├── .env             # Database password helper
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
| `antline init [--path DIR] [--name NAME] --db-type TYPE --host H --port P --user U --password PWD [--database DB]` | Initialize workspace with platform config |
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
| `antline requirement create --name NAME --target-schema PATH ...` | Create a requirement (multiple files or dirs) |
| `antline requirement list [--json]` | List all requirements |
| `antline requirement show REQ-xxx` | Show requirement details |
| `antline requirement assess REQ-xxx SRC-xxx [SRC-yyy ...] [--focus TABLES] [--full]` | Generate LLM prompt + human guide + Markdown template for review |
| `antline requirement approve REQ-xxx [--file PATH] [--force]` | Confirm requirement after reviewing assessment.md |
| `antline requirement update REQ-xxx ...` | Update requirement (resets assessment) |
| `antline requirement remove REQ-xxx [--force]` | Remove a requirement |

### Project Management

| Command | Description |
|---------|-------------|
| `antline project create --name NAME --requirements REQ-xxx` | Create project from approved requirements |
| `antline project list [--json]` | List all projects |
| `antline project show PRJ-xxx` | Show project details |
| `antline project scaffold PRJ-xxx [--source-mode {fdw\|sync}] [--skip-db-setup]` | Generate dbt project scaffolding (uses workspace platform config) |
| `antline project compile PRJ-xxx [-m MODEL]` | Validate SQL syntax without executing |
| `antline project build PRJ-xxx` | Build with dbt |
| `antline project validate PRJ-xxx` | Run data quality tests |
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
  --db-type postgresql --host localhost --port 5432 \
  --user wbg --password '***' --database hospital_data
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
# Add HIS system database (connection is validated before saving)
antline source add --type postgresql --host db.hospital.local \
  --database his_db --user wbg --password '***'

# Explore structure (generates both report.yml for agents and report.md for humans)
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
# Create requirement from target schema(s)
antline requirement create --name "统一患者视图" \
  --target-schema target_schema/patients.yaml \
  --background "医院 HIS 和 EMR 系统患者数据分散，需要统一视图" \
  --goal "建立 MIMIC-IV 标准的 patients + admissions 维度表"

# Or create from a whole directory
antline requirement create --name "全量住院视图" \
  --target-schema target_schema/hosp/

# Assess feasibility — generates prompts and template (does NOT auto-map)
# Default: table/field metadata only, no statistics
antline requirement assess REQ-20260508-001 SRC-20260508-001

# Focus on specific tables only
antline requirement assess REQ-20260508-001 SRC-20260508-001 --focus patient_info,admission_records

# Include full field statistics (null rates, unique counts, top values)
antline requirement assess REQ-20260508-001 SRC-20260508-001 --full

# Review the generated materials, save as assessment.md, then approve
antline requirement approve REQ-20260508-001
```

**Design note:** `assess` does NOT auto-generate field mappings. It produces
files in `requirements/REQ-xxx/assessment/` for human or LLM review:

| File | Purpose |
|------|---------|
| `prompt.md` | LLM prompt with target schema + source metadata |
| `guide.md` | Human-readable assessment guide |
| `template.md` | Empty Markdown template with YAML frontmatter |

Copy the prompt to an LLM, review the output, save it as `assessment.md`,
then run `approve` to store the assessment in the requirement.

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
antline project create --name "患者数据集成项目" --requirements REQ-20260508-001

# Scaffold (uses workspace platform config automatically)
antline project scaffold PRJ-20260508-001

# Setup FDW (for fdw mode)
psql -d hospital_data -f projects/PRJ-20260508-001/dbt/sql/fdw_setup.sql

# Source environment variables
set -a && source projects/PRJ-20260508-001/.env && set +a
```

Generated dbt models:
```sql
-- projects/PRJ-20260508-001/dbt/models/map/map_patients.sql
-- Map layer: patients
-- Requirement: REQ-20260508-001

SELECT
    patient_id AS subject_id,  -- transform from patients
    gender AS gender,  -- direct from patients
    age AS anchor_age,  -- transform from patients
    EXTRACT(YEAR FROM birthday) AS anchor_year,  -- TODO: fix syntax
    NULL AS anchor_year_group,  -- missing: no source mapping
    NULL AS dod  -- missing: no source mapping
FROM {{ ref('row_patients') }}
```

### 6. Compile, Build and Validate

```bash
# Validate SQL syntax without executing (fast)
antline project compile PRJ-20260508-001

# Build with dbt
cd projects/PRJ-20260508-001/dbt && dbt build

# Validate and deliver
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
# 1. Agent initializes workspace
run("antline init --name X --db-type postgresql --host localhost --port 5432 --user postgres --password '***' --database mydb")

# 2. Agent reads source metadata
report = yaml.safe_load(run("antline source explore SRC-20260508-001 --json"))

# 3. Agent defines requirement with background + goal
run(
    "antline requirement create --name X "
    "--target-schema schema.yaml "
    "--background '...' --goal '...'"
)

# 4. Agent generates assessment materials (prompt + template)
run("antline requirement assess REQ-20260508-001 SRC-20260508-001")

# 5. Agent reads the prompt, generates assessment.md
prompt = open("requirements/REQ-20260508-001/assessment/prompt.md").read()
assessment_md = llm_generate_assessment(prompt)  # agent logic
with open("requirements/REQ-20260508-001/assessment/assessment.md", "w") as f:
    f.write(assessment_md)

# 6. Agent approves the completed assessment
run("antline requirement approve REQ-20260508-001")

# 7. Create project and scaffold
run("antline project create --name X --requirements REQ-20260508-001")
run("antline project scaffold PRJ-20260508-001 --source-mode fdw")

# 8. Compile and build
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
- [ ] Extract job (physical data sync for sync mode)
- [ ] Password encryption for source configs
- [ ] Schedule command (cron wrapper / Airflow DAG generation)
- [ ] Plugin system for custom ETL backends
- [ ] Web UI (lightweight)

## License

Apache-2.0
