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
- **Lightweight**: Delegates execution to dbt; Antline manages the workflow layer
- **Open source**: Apache-2.0, built for independent developers and small teams

## Quick Start

```bash
# Install
pip install antline[all]

# Initialize a project
git init my-data-project && cd my-data-project
antline init

# Add a data source
antline source add --type postgresql --host localhost --port 5432 \
  --database mydb --user myuser

# Explore the source
antline source explore SRC-001

# Import target schema from CSV (e.g. MIMIC-IV standard)
antline schema import /path/to/standard_schema.csv --output-dir target_schema

# Define a requirement (single table)
antline requirement create --name "Unified patient view" \
  --target-schema target_schema/patients.yaml \
  --background "Hospital needs a unified patient dimension" \
  --goal "Build a standardized patients table from HIS + EMR"

# Or multiple tables / a whole directory
antline requirement create --name "Inpatient full view" \
  --target-schema target_schema/patients.yaml \
  --target-schema target_schema/admissions.yaml \
  --background "..." --goal "..."

# Assess feasibility against source data (draft for review)
antline requirement assess REQ-001 SRC-001

# Create project and scaffold pipeline
antline project create --name "Patient 360" --requirement REQ-001

# Scaffold with interactive database setup
antline project scaffold PRJ-001

# Or scaffold with all parameters (for CI/automation)
antline project scaffold PRJ-001 \
  --db-type postgresql --host localhost --port 5432 \
  --user wbg --password '***' \
  --source-mode fdw

# Compile (validate SQL syntax without executing)
antline project compile PRJ-001
antline project compile PRJ-001 -m map_patients

# Build with dbt
cd dbt/PRJ-001 && dbt build

# Validate and deliver
antline project validate PRJ-001
antline project deliver PRJ-001
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
                                        └─────────────┘
```

| Layer | Technology | Role |
|-------|------------|------|
| CLI | Typer + Rich | User interface (human + agent) |
| State | Git-native YAML | Zero-database state management |
| Models | Pydantic | Type-safe data entities |
| DB | SQLAlchemy | Multi-database metadata reflection |
| Execution | External dbt | SQL transformation engine |

## Command Reference

### Global

| Command | Description |
|---------|-------------|
| `antline init [--path DIR] [--name NAME]` | Initialize a new Antline project |
| `antline status` | Show project overview (sources, requirements, projects) |

### Source Management

| Command | Description |
|---------|-------------|
| `antline source add --type {postgresql\|mysql\|tidb} ...` | Add a data source (validates connection) |
| `antline source list [--json]` | List all sources |
| `antline source explore SRC-001 [--max-tables N] [--no-mask]` | Explore metadata + statistics (generates `.yml` + `.md`) |
| `antline source show SRC-001` | Show source details |
| `antline source update SRC-001 --host newhost ...` | Update source fields |
| `antline source remove SRC-001 [--force]` | Remove a source |

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
| `antline requirement show REQ-001` | Show requirement details |
| `antline requirement assess REQ-001 SRC-001 [SRC-002 ...] [--focus TABLES] [--full]` | Generate LLM prompt + human guide + Markdown template for review |
| `antline requirement approve REQ-001 [--file PATH] [--force]` | Confirm requirement after reviewing assessment.md |
| `antline requirement update REQ-001 ...` | Update requirement (resets assessment) |
| `antline requirement remove REQ-001 [--force]` | Remove a requirement |

### Project Management

| Command | Description |
|---------|-------------|
| `antline project create --name NAME --requirements REQ-001` | Create project from approved requirements |
| `antline project list [--json]` | List all projects |
| `antline project show PRJ-001` | Show project details |
| `antline project scaffold PRJ-001 [--db-type TYPE] [--host H] [--port P] [--user U] [--password PWD] [--db-name DB] [--source-mode {fdw\|sync}]` | Generate dbt project scaffolding |
| `antline project compile PRJ-001 [-m MODEL]` | Validate SQL syntax without executing |
| `antline project build PRJ-001` | Build with dbt |
| `antline project validate PRJ-001` | Run data quality tests |
| `antline project deliver PRJ-001` | Mark as production-ready |

## Project Structure

```
my-project/
├── antline.yml              # Project configuration
├── .gitignore               # Excludes passwords, generated reports
├── .env.prj-001             # Database password helper (auto-generated)
├── sources/
│   ├── SRC-001.yml          # Data source configurations
│   └── SRC-002.yml
├── requirements/
│   ├── REQ-001.yml          # Data requirement definitions
│   └── REQ-002.yml
├── projects/
│   └── PRJ-001.yml          # Project definitions
├── target_schema/           # Target data standard (YAML)
│   ├── patients.yaml
│   └── admissions.yaml
├── dbt/                     # Per-project dbt directories
│   └── PRJ-001/
│       ├── dbt_project.yml
│       ├── profiles.yml
│       ├── sql/
│       │   └── fdw_setup.sql      # PostgreSQL FDW setup script
│       └── models/
│           ├── row/             # Row layer (auto from sources)
│           ├── map/             # Map layer (template from requirements)
│           ├── clean/           # Clean layer (template + tests)
│           └── sources.yml
└── reports/
    ├── SRC-001_explore.yml  # Source exploration report (structured, for agents)
    ├── SRC-001_explore.md   # Source exploration report (human-readable)
    └── assessment/          # Feasibility assessment materials
        ├── REQ-001_prompt.md                 # LLM prompt (target schema + source metadata)
        ├── REQ-001_guide.md                  # Human-readable assessment guide
        ├── REQ-001_template.md               # Empty Markdown template with YAML frontmatter
        └── REQ-001_assessment.md             # Completed Markdown assessment (after review)
```

## Scaffold: Row Layer Source Modes

When scaffolding a project, row layer models can reference source tables in two ways:

### FDW Mode (default)

Uses PostgreSQL Foreign Data Wrapper to query external databases as foreign tables.

```bash
antline project scaffold PRJ-001 --source-mode fdw
```

Prerequisites:
1. Run the auto-generated FDW setup script before `dbt build`:
   ```bash
   psql -d prj_001 -f dbt/PRJ-001/sql/fdw_setup.sql
   ```
2. This creates foreign tables in schemas named after the source databases (e.g. `his_db.patients`)

### Sync Mode

Expects data to be physically synced into the target database's ODS layer first.

```bash
antline project scaffold PRJ-001 --source-mode sync
```

Prerequisites:
1. Run extract job to copy source data into target DB's `ods_src_001` schema
2. Then `dbt build` queries local ODS tables

## Workflow Example: Hospital Data Integration

### 1. Define Target Standard

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

### 2. Explore Source Databases

```bash
# Add HIS system database (connection is validated before saving)
antline source add --type postgresql --host db.hospital.local \
  --database his_db --user wbg --password '***'

# Explore structure (generates both .yml for agents and .md for humans)
# Sample data in sensitive fields is masked by default
antline source explore SRC-001

# Or disable masking when you need raw values
antline source explore SRC-001 --no-mask
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

### 3. Create, Assess, and Approve Requirements

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
antline requirement assess REQ-001 SRC-001 SRC-002

# Focus on specific tables only
antline requirement assess REQ-001 SRC-001 --focus patient_info,admission_records

# Include full field statistics (null rates, unique counts, top values)
antline requirement assess REQ-001 SRC-001 --full

# Review the generated materials, save as assessment.md, then approve
antline requirement approve REQ-001
```

**Design note:** `assess` does NOT auto-generate field mappings. It produces
three files for human or LLM review:

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
  LLM 提示词:  reports/assessment/REQ-001_prompt.md
  人工指南:    reports/assessment/REQ-001_guide.md
  评估模板:    reports/assessment/REQ-001_template.md

下一步操作:
  1. 将 prompt.md 的内容复制给大模型，获取评估结果
  2. 人工审核修改后，保存为 assessment.md
  3. 审批通过: antline requirement approve REQ-001
```

### 4. Create Project and Scaffold

```bash
antline project create --name "患者数据集成项目" --requirements REQ-001

# Interactive scaffold (prompts for database connection)
antline project scaffold PRJ-001

# Or non-interactive
antline project scaffold PRJ-001 \
  --db-type postgresql \
  --host localhost --port 5432 \
  --user wbg --password '***'

# Setup FDW (for fdw mode)
psql -d prj_001 -f dbt/PRJ-001/sql/fdw_setup.sql

# Source environment variables
set -a && source .env.prj-001 && set +a
```

Generated dbt models:
```sql
-- dbt/PRJ-001/models/map/map_patients.sql
-- Map layer: patients
-- Requirement: REQ-001

SELECT
    patient_id AS subject_id,  -- transform from patients
    gender AS gender,  -- direct from patients
    age AS anchor_age,  -- transform from patients
    EXTRACT(YEAR FROM birthday) AS anchor_year,  -- TODO: fix syntax
    NULL AS anchor_year_group,  -- missing: no source mapping
    NULL AS dod  -- missing: no source mapping
FROM {{ ref('row_patients') }}
```

### 5. Compile, Build and Validate

```bash
# Validate SQL syntax without executing (fast)
antline project compile PRJ-001

# Build with dbt
cd dbt/PRJ-001 && dbt build

# Validate and deliver
antline project validate PRJ-001
antline project deliver PRJ-001
```

## Agent API Guide

Antline is designed for LLM/Agent consumption. Every command supports `--json`:

```bash
# List sources as JSON
antline source list --json

# List requirements as JSON
antline requirement list --json

# Explore and output raw YAML
antline source explore SRC-001 --json
```

**Agent workflow pattern:**

```python
# 1. Agent reads source metadata
report = yaml.safe_load(run("antline source explore SRC-001 --json"))

# 2. Agent defines requirement with background + goal
run(
    "antline requirement create --name X "
    "--target-schema schema.yaml "
    "--background '...' --goal '...'"
)

# 3. Agent generates assessment materials (prompt + template)
run("antline requirement assess REQ-001 SRC-001 SRC-002")

# 4. Agent reads the prompt, generates assessment.md
prompt = open("reports/assessment/REQ-001_prompt.md").read()
assessment_md = llm_generate_assessment(prompt)  # agent logic
with open("reports/assessment/REQ-001_assessment.md", "w") as f:
    f.write(assessment_md)

# 5. Agent approves the completed assessment
run("antline requirement approve REQ-001")

# 6. Create project and scaffold
run("antline project create --name X --requirements REQ-001")
run("antline project scaffold PRJ-001 --source-mode fdw")

# 7. Compile and build
run("antline project compile PRJ-001")
run("antline project build PRJ-001")
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
git clone https://github.com/antline-dev/antline.git
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
- [x] Per-project dbt directories (`dbt/PRJ-001/`)
- [x] Interactive scaffold with database setup prompts
- [x] FDW / sync dual source modes for row layer
- [x] SQL compile command (validate without executing)
- [x] dbt integration (build, validate)
- [x] Connection validation for source add
- [x] PII-aware data masking in explore reports
- [x] Dual-format reports (YAML for agents + Markdown for humans)
- [ ] Extract job (physical data sync for sync mode)
- [ ] Password encryption for source configs
- [ ] Schedule command (cron wrapper)
- [ ] Plugin system for custom ETL backends
- [ ] Web UI (lightweight)

## License

Apache-2.0
