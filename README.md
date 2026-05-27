# Antline

> CLI 数据生产管理工具 —— 从数据源探查到交付。

Antline 为数据工程引入项目管理规范，为数据团队（及 Agent）提供结构化的 CLI 工作流：

1. **探查** 数据源 —— 元数据、统计信息、样本数据
2. **定义** 数据需求 —— 从 CSV 或 YAML 导入目标标准
3. **评估** 可行性 —— LLM 驱动的智能分析，采用"生成 → 审计 → 修补"反馈循环，直接产出模型级 SQL
4. **构建** 数据管道 —— dbt 原生脚手架（row / map / clean 三层）
5. **验证** 数据质量 —— dbt 测试 + 自定义检查
6. **交付** 生产数据 —— 版本化、可审计、可复现

## 为什么选择 Antline？

- **Agent 优先**：结构化 CLI 输出专为 LLM/Agent 消费设计（每个命令支持 `--json`）
- **对人友好**：交互式提示和丰富的报告，支持手动工作流
- **Git 原生**：所有状态以 YAML 文件存储 —— 用版本控制管理数据项目
- **工作空间为中心**：一个工作空间 = 一个数据平台，所有项目共享同一个目标数据库
- **安全优先**：任何配置文件均不存储凭据；所有密码运行时提示，并记录审计日志
- **轻量**：执行委托给 dbt；Antline 只管理工作流层
- **开源**：Apache-2.0，面向独立开发者和小团队

## 快速开始

```bash
# 安装
pip install antline[all]

# 初始化工作空间（目标数据库平台配置）
mkdir my-data-workspace && cd my-data-workspace
antline init --name "医院数据团队" \
  --db-type postgresql --host localhost --port 5432

# 添加数据源（密码运行时提示）
antline source add --type postgresql --host localhost --port 5432 \
  --database mydb --user myuser

# 探查数据源
antline source explore SRC-20260508-001

# 在本地接入源数据（FDW 外联表或物理同步）
antline source setup SRC-20260508-001 --mode fdw

# 从 CSV 导入目标标准（例如 MIMIC-IV 标准）
antline schema import /path/to/standard_schema.csv --output-dir target_schema

# 定义需求
antline requirement create --name "统一患者视图" \
  --background "医院需要统一的患者维度表" \
  --goal "从 HIS + EMR 构建标准化的 patients 表"

# 为目标表添加标准（YAML 文件、目录或 CSV）
antline requirement add-schema REQ-20260508-001 target_schema/patients.yaml
antline requirement add-schema REQ-20260508-001 target_schema/hosp/
antline requirement add-schema REQ-20260508-001 standard_schema.csv

# 评估可行性（两种模式）

# 模式 A：LLM 自动分析（推荐用于 Agent）
# 运行 5 步流水线：表范围 → SQL 生成 → 覆盖审计 → 缺口填补 → 合并
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto

# 自动分析 + SQL 校验（需在本地接入源数据后使用）
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --validate

# 模式 B：人工审核（生成 prompt.md + guide.md + template.md）
antline requirement assess REQ-20260508-001 SRC-20260508-001
# 审阅评估材料并保存为 assessment.md 后：
antline requirement approve REQ-20260508-001

# 创建项目并搭建管道
antline project create --name "患者 360" --requirement REQ-20260508-001

# 搭建（凭据运行时提示，永不存储）
antline project scaffold PRJ-20260508-001 --user myuser --password '***'

# 编译（不执行，仅验证 SQL 语法）
antline project compile PRJ-20260508-001
antline project compile PRJ-20260508-001 -m map_patients

# 使用 dbt 构建（凭据运行时提示）
antline project build PRJ-20260508-001

# 验证并交付（凭据运行时提示）
antline project validate PRJ-20260508-001
antline project deliver PRJ-20260508-001 --user postgres --password '***'
```

## 安装

```bash
# 基础安装（仅 PostgreSQL）
pip install antline[postgres]

# 支持 MySQL/TiDB
pip install antline[mysql,tidb]

# 全部数据库驱动 + 开发工具
pip install antline[all,dev]
```

**环境要求：**
- Python 3.10+
- Git（用于版本控制）
- dbt（用于 SQL 执行，单独安装：`pip install dbt-core dbt-postgres`）
- PostgreSQL（目标数据库，若使用 FDW 模式）

## 架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   数据源    │────▶│    需求     │────▶│    项目     │
│   管理      │     │   管理      │     │   管理      │
└─────────────┘     └─────────────┘     └─────────────┘
                                                │
                                                ▼
                                        ┌─────────────┐
                                        │  dbt / SQL  │
                                        │   执行层    │
└───────────────────────────────────────────────────────┘
│            工作空间平台（共享数据库）                   │
└───────────────────────────────────────────────────────┘
```

| 层级 | 技术 | 职责 |
|------|------|------|
| CLI | Typer + Rich | 用户界面（人 + Agent） |
| 状态 | Git 原生 YAML | 零数据库状态管理 |
| 模型 | Pydantic | 类型安全的数据实体 |
| 数据库 | SQLAlchemy | 多数据库元数据反射 |
| 执行 | 外部 dbt | SQL 转换引擎 |
| 平台 | 工作空间级 | 共享目标数据库配置 |

## 工作空间结构

```
my-workspace/
├── antline.yml              # 工作空间配置 + 平台
├── .gitignore               # 排除密码、生成的报告
├── sources/
│   └── SRC-20260508-001/
│       ├── source.yml       # 数据源配置
│       └── explore/
│           ├── report.yml   # 结构化报告（面向 Agent）
│           └── report.md    # 可读报告（面向人）
├── requirements/
│   └── REQ-20260508-001/
│       ├── requirement.yml  # 需求定义
│       ├── target_schema/   # 目标数据标准 YAML
│       └── assessment/
│           ├── prompt.md    # LLM 提示词
│           ├── guide.md     # 人工指南
│           ├── template.md  # 空白模板
│           └── assessment.md # 完成的评估
├── projects/
│   └── PRJ-20260508-001/
│       ├── project.yml      # 项目定义
│       ├── dbt/             # 项目级 dbt 目录
│       │   ├── dbt_project.yml
│       │   ├── profiles.yml
│       │   └── models/
│       │       ├── row/     # 行层
│       │       ├── map/     # 映射层
│       │       ├── clean/   # 清洗层
│       │       └── sources.yml
│       └── qc/
│           └── report.md    # 质检报告
└── reports/                 # 工作空间级报告
```

### ID 格式

所有实体使用基于日期的 ID：
- `SRC-YYYYMMDD-NNN` —— 例如 `SRC-20260508-001`
- `REQ-YYYYMMDD-NNN` —— 例如 `REQ-20260508-001`
- `PRJ-YYYYMMDD-NNN` —— 例如 `PRJ-20260508-001`

ID 在同一日期内顺序递增，跨日期互不干扰。

## 命令参考

### 全局

| 命令 | 说明 |
|------|------|
| `antline --version` | 显示版本 |
| `antline init [--path DIR] [--name NAME] --db-type TYPE --host H --port P [--user U] [--password PWD] [--no-test-connection]` | 初始化工作空间（测试连接，凭据不存储） |
| `antline status` | 显示工作空间概览（数据源、需求、项目） |

### 数据源管理

| 命令 | 说明 |
|------|------|
| `antline source add --type {postgresql\|mysql\|tidb} ...` | 添加数据源（验证连接） |
| `antline source list [--json]` | 列出所有数据源 |
| `antline source explore SRC-xxx [--max-tables N] [--no-mask]` | 探查元数据 + 统计信息（生成 `explore/report.yml` + `report.md`） |
| `antline source show SRC-xxx` | 显示数据源详情 |
| `antline source update SRC-xxx --host newhost ...` | 更新数据源字段 |
| `antline source setup SRC-xxx --mode {fdw|sync} [--tables TBL,TBL] [--target-db DB] [--target-user U] [--target-password PWD] [--source-password PWD] [--batch-size N]` | 在本地目标数据库接入源数据。`fdw`: 创建外联表; `sync`: 物理同步到 ODS 层 |
| `antline source remove SRC-xxx [--force]` | 删除数据源 |

### 标准管理

| 命令 | 说明 |
|------|------|
| `antline schema import CSV_FILE [--output-dir DIR]` | 从 CSV 导入目标标准 |
| `antline schema list` | 列出已导入的标准 |
| `antline schema show TABLE_NAME` | 显示标准定义 |

### 需求管理

| 命令 | 说明 |
|------|------|
| `antline requirement create --name NAME [--background TEXT] [--goal TEXT]` | 创建需求 |
| `antline requirement list [--json]` | 列出所有需求 |
| `antline requirement show REQ-xxx` | 显示需求详情 |
| `antline requirement add-schema REQ-xxx PATH [PATH ...]` | 向需求添加目标标准 YAML、目录或 CSV |
| `antline requirement assess REQ-xxx SRC-xxx [SRC-yyy ...] [--focus TABLES] [--full] [--auto] [--step {scope\|generate}] [--scope-file PATH] [--json] [--min-confidence N] [--validate] [--target-password PWD]` | 生成评估。默认：prompt.md + guide.md + template.md。`--auto`：LLM 驱动的 5 步分析，产出模型 SQL + 清洗规则。`--validate`：SQL 语法+字段校验（需先 source setup） |
| `antline requirement approve REQ-xxx [--file PATH] [--force] [--note TEXT]` | 审阅 assessment.md 后确认需求。验证 source_table/field 引用是否与探查报告一致。`--force` 跳过验证或为 `IN_PROJECT` 需求重新审批（需 `--note`） |
| `antline requirement update REQ-xxx ...` | 更新需求（重置评估） |
| `antline requirement remove REQ-xxx [--force]` | 删除需求 |

### 项目管理

| 命令 | 说明 |
|------|------|
| `antline project create --name NAME --requirements REQ-xxx` | 从已审批需求创建项目 |
| `antline project list [--json]` | 列出所有项目 |
| `antline project show PRJ-xxx` | 显示项目详情 |
| `antline project scaffold PRJ-xxx [--source-mode {fdw\|sync}] [--skip-db-setup] [--user U] [--password PWD]` | 生成 dbt 项目脚手架（未提供凭据时提示） |
| `antline project compile PRJ-xxx [-m MODEL] [--user U] [--password PWD]` | 不执行，仅验证 SQL 语法 |
| `antline project build PRJ-xxx [--user U] [--password PWD]` | 使用 dbt 构建 |
| `antline project validate PRJ-xxx [--user U] [--password PWD]` | 运行数据质量测试 |
| `antline project deliver PRJ-xxx [--user U] [--password PWD] [--strategy {atomic\|replace}] [--clean-schema SCHEMA] [--prod-schema SCHEMA] [--tables TBL,TBL] [--dry-run]` | 将 clean 层数据交付到 prod schema。atomic: 零停机重命名; replace: 直接替换 |

## 脚手架：行层数据源模式

搭建项目时，行层模型可以通过两种方式引用源表：

### FDW 模式（默认）

使用 PostgreSQL Foreign Data Wrapper 将外部数据库作为外部表查询。

```bash
antline project scaffold PRJ-20260508-001 --source-mode fdw
```

前置条件：
1. 在 `dbt build` 之前运行自动生成的 FDW 设置脚本：
   ```bash
   psql -d antline_workspace -f projects/PRJ-20260508-001/dbt/sql/fdw_setup.sql
   ```
2. 这将创建以源数据库命名的 schema 中的外部表（例如 `his_db.patients`）

### Sync 模式

期望数据先物理同步到目标数据库的 ODS 层。

```bash
antline project scaffold PRJ-20260508-001 --source-mode sync
```

前置条件：
1. 运行抽取作业将源数据复制到目标数据库的 `ods_src_001` schema
2. 然后 `dbt build` 查询本地 ODS 表

## 工作流示例：医院数据集成

### 1. 初始化工作空间

```bash
antline init --name "医院数据团队" \
  --db-type postgresql --host localhost --port 5432
```

### 2. 定义目标标准

创建包含目标数据标准的 CSV 文件：

```csv
module,table_name,table_comment,field_name,field_type,field_comment,example
Hosp,patients,患者信息,subject_id,INTEGER NOT NULL,患者唯一标识符,10000032
Hosp,patients,患者信息,gender,VARCHAR(1),性别,F; M
Hosp,patients,患者信息,age,INTEGER,年龄,65
```

导入：

```bash
antline schema import hospital_standard.csv --output-dir target_schema
```

### 3. 探查源数据库

```bash
# 添加 HIS 系统数据库（保存前验证连接，密码运行时提示）
antline source add --type postgresql --host db.hospital.local \
  --database his_db --user wbg

# 探查结构（同时生成面向 Agent 的 report.yml 和面向人的 report.md）
# 密码运行时提示，永不存储
# 敏感字段的样本数据默认脱敏
antline source explore SRC-20260508-001

# 或需要原始值时关闭脱敏
antline source explore SRC-20260508-001 --no-mask
```

输出：
```
数据库: his_db (postgresql)
表: 47 | 行数: 1,234,567 | 列数: 892

┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ 表名               ┃ 模式   ┃ 行数     ┃ 列数    ┃ 主键    ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ patient_visits     │ public │ 500,000  │ 12      │ visit_id│
│ patient_info       │ public │ 120,000  │ 8       │ id      │
│ ...                │ ...    │ ...      │ ...     │ ...     │
└────────────────────┴────────┴──────────┴─────────┴─────────┘
```

### 4. 创建、评估并审批需求

```bash
# 1. 创建需求（背景 + 目标）
antline requirement create --name "统一患者视图" \
  --background "医院 HIS 和 EMR 系统患者数据分散，需要统一视图" \
  --goal "建立 MIMIC-IV 标准的 patients + admissions 维度表"

# 2. 向需求添加目标标准（YAML、目录或 CSV）
antline requirement add-schema REQ-20260508-001 target_schema/patients.yaml
antline requirement add-schema REQ-20260508-001 target_schema/hosp/
antline requirement add-schema REQ-20260508-001 hospital_standard.csv

# 3. 评估可行性 —— 两种模式可选：

# 模式 A：LLM 自动分析（推荐用于 Agent）
# 运行 5 步流水线：表范围 → SQL 生成 → 覆盖审计 → 缺口填补 → 合并
# 直接产出模型级 SQL + 清洗规则，无需手动填写模板
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto

# 自动分析 + SQL 校验（需先运行 source setup 接入源数据）
# 校验内容：source 可用性检测 → EXPLAIN 语法校验 → LIMIT 1 字段校验
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --validate

# 分步执行：仅分析表范围（第 1 步），输出 JSON
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step scope --json

# 分步执行：从已有的范围文件生成 SQL（第 2-5 步）
antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step generate --scope-file scope.json

# 模式 B：人工审核（生成 prompt.md + guide.md + template.md）
antline requirement assess REQ-20260508-001 SRC-20260508-001

# 仅关注特定表
antline requirement assess REQ-20260508-001 SRC-20260508-001 --focus patient_info,admission_records

# 包含完整字段统计（空值率、唯一值数、Top 值）
antline requirement assess REQ-20260508-001 SRC-20260508-001 --full

# 4. 审批
# 自动评估：直接审批（评估结果已在 requirement.yml 中）
antline requirement approve REQ-20260508-001

# 人工评估：审阅材料，保存为 assessment.md，然后审批
# 审批时会验证 source_table/source_field 是否与探查报告一致
antline requirement approve REQ-20260508-001

# 若验证失败（如表/字段在探查报告中不存在），
# 修复 assessment.md 或使用 --force 跳过
antline requirement approve REQ-20260508-001 --force
```

**设计说明：** `assess` 支持两种模式：

| 模式 | 标志 | 输出 | 适用场景 |
|------|------|------|----------|
| 自动（LLM） | `--auto` | `model_sqls` + `clean_rules` + `field_mappings` + `scope.json` | Agent / 自动化流水线 |
| 人工 | （默认） | `prompt.md` + `guide.md` + `template.md` | 人工审核 / 外部 LLM |

| 文件 | 用途 |
|------|------|
| `prompt.md` | 包含目标标准 + 源元数据的 LLM 提示词 |
| `guide.md` | 可读性评估指南 |
| `template.md` | 带 YAML 前置 matter 的空白 Markdown 模板 |

将 prompt 复制给 LLM，审阅输出，保存为 `assessment.md`，
然后运行 `approve` 将评估结果存入需求。

**自动评估 5 步流水线**（`--auto`）：

```
第 0 步：上下文准备      → 将探查报告汇总为 LLM 友好文本
第 1 步：表范围分析      → 哪些源表 feeding 每个目标表（含 JOIN 关系）
第 2 步：模型 SQL 生成   → 每个目标表的完整 dbt 模型 SQL
第 3 步：覆盖审计        → AST 解析 SQL，与目标标准 diff（确定性，无 LLM）
第 4 步：缺口搜索        → 在所有源表中查找未覆盖字段的映射
第 5 步：模型合并        → 将缺口增量修补回 SQL
```

优势：
- **Token 高效**：第 1 步使用宽上下文（所有表），第 2-5 步使用窄上下文（仅范围表）
- **高覆盖率**：审计 + 缺口填补确保没有目标字段被遗漏
- **模型级 SQL**：输出是完整的 `SELECT ... FROM ... JOIN ...` 语句，而非逐字段映射
- **清洗规则**：自动为清洗层生成 `clean_rules`（CAST、COALESCE、TRIM、UPPER 等）
- **审计轨迹**：产出 `scope.json` + 每模型的 `.sql` 文件供人工审阅

**审批验证：** 在 `approve` 过程中，Antline 会交叉检查每个
非 `missing` 的映射与源探查报告：
- `source_table` 必须存在于对应数据源的探查报告中
- `source_field` 必须存在于该表的列中

若验证失败，将打印带行号的错误。使用 `--force` 强制通过
（例如针对计划中的未来 schema 变更）。

**重新审批：** 若需求已在项目中（`IN_PROJECT` 状态），
可使用 `--force` 和 `--note` 说明原因进行重新审批：
```bash
antline requirement approve REQ-20260508-001 --force --note "修正表名: visits -> inpatient_visits"
```
状态保持 `IN_PROJECT`，备注记录在评估中。

评估输出：
```
评估材料已生成：
  LLM 提示词:  requirements/REQ-20260508-001/assessment/prompt.md
  人工指南:    requirements/REQ-20260508-001/assessment/guide.md
  评估模板:    requirements/REQ-20260508-001/assessment/template.md

下一步操作：
  1. 将 prompt.md 的内容复制给大模型，获取评估结果
  2. 人工审核修改后，保存为 assessment.md
  3. 审批通过: antline requirement approve REQ-20260508-001
```

### 5. 创建项目并搭建

```bash
antline project create --name "患者数据集成项目" --requirement REQ-20260508-001

# 搭建（凭据运行时提示，永不存储）
antline project scaffold PRJ-20260508-001

# 设置 FDW（fdw 模式）
psql -d hospital_data -f projects/PRJ-20260508-001/dbt/sql/fdw_setup.sql
```

生成的 dbt 模型（使用 `--auto`，模型级 SQL）：

```sql
-- projects/PRJ-20260508-001/dbt/models/map/map_patients.sql
-- 映射层: patients
-- 需求: REQ-20260508-001

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

清洗层（带 `clean_rules`）：

```sql
-- projects/PRJ-20260508-001/dbt/models/clean/clean_patients.sql
-- 清洗层: patients

SELECT
    patient_id,
    UPPER(COALESCE(TRIM(gender), 'U')) AS gender,
    CAST(anchor_age AS INTEGER) AS anchor_age,
    CAST(anchor_year AS INTEGER) AS anchor_year,
    anchor_year_group,
    dod
FROM {{ ref('map_patients') }}
```

### 6. 编译、构建和验证

```bash
# 不执行，仅验证 SQL 语法（快速，凭据运行时提示）
antline project compile PRJ-20260508-001

# 使用 dbt 构建（凭据运行时提示）
antline project build PRJ-20260508-001

# 验证并交付（凭据运行时提示）
antline project validate PRJ-20260508-001

# 原子交付: clean 层 → prod 层 (零停机, 凭据运行时提示)
antline project deliver PRJ-20260508-001 --user postgres --password '***'

# 预览交付 (dry-run)
antline project deliver PRJ-20260508-001 --dry-run

# 仅交付指定表
antline project deliver PRJ-20260508-001 --tables patients,admissions
```

## Agent API 指南

Antline 专为 LLM/Agent 消费设计。每个命令支持 `--json`：

```bash
# JSON 格式列出数据源
antline source list --json

# JSON 格式列出需求
antline requirement list --json

# 探查并输出原始 YAML
antline source explore SRC-20260508-001 --json
```

**Agent 工作流模式：**

```python
# 1. Agent 初始化工作空间（不存储凭据）
run("antline init --name X --db-type postgresql --host localhost --port 5432")

# 2. Agent 读取源元数据（密码运行时提示）
report = yaml.safe_load(run("antline source explore SRC-20260508-001 --json"))

# 3. Agent 定义需求（背景 + 目标）
run(
    "antline requirement create --name X "
    "--background '...' --goal '...'"
)
run("antline requirement add-schema REQ-20260508-001 schema.yaml")

# 4. Agent 运行自动评估（5 步 LLM 流水线）
#    产出 model_sqls + clean_rules + field_mappings + scope.json
result = json.loads(run(
    "antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --json"
))

# 5. Agent 审阅未覆盖字段（如有）并决定是否审批
if result["approval_recommendation"] == "auto" and not result["uncovered_fields"]:
    run("antline requirement approve REQ-20260508-001")
else:
    # 审阅低置信度映射或未覆盖字段
    for f in result["uncovered_fields"]:
        print(f"未覆盖: {f}")
    # Agent 可修复单个映射，或使用 --force 强制审批
    run("antline requirement approve REQ-20260508-001 --force")

# 替代方案：分步执行以精细控制
# 第 1 步：仅分析表范围
run("antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step scope")
# Agent 审阅 scope.json，然后第 2-5 步：从范围生成 SQL
run("antline requirement assess REQ-20260508-001 SRC-20260508-001 --auto --step generate --scope-file scope.json")

# 7. 创建项目并搭建
run("antline project create --name X --requirements REQ-20260508-001")
run("antline project scaffold PRJ-20260508-001 --source-mode fdw")

# 8. 编译和构建（凭据运行时提示）
run("antline project compile PRJ-20260508-001")
run("antline project build PRJ-20260508-001")
```

## CSV 标准格式

Antline 接受包含以下列的 CSV 文件：

| 列名 | 说明 |
|------|------|
| `module` | 业务模块名（例如 `Hosp`、`ICU`） |
| `table_name` | 目标表名 |
| `table_comment` | 表说明 |
| `field_name` | 字段/列名 |
| `field_type` | 数据类型，可选 `NOT NULL`（例如 `INTEGER NOT NULL`） |
| `field_comment` | 字段说明 |
| `example` | 示例值 |

## 开发

```bash
# 克隆
git clone https://github.com/wangbigan/antline.git
cd antline

# 可编辑模式安装
pip install -e ".[all,dev]"

# 运行测试
pytest tests/ -v

# 格式化和检查
ruff format antline/ tests/
ruff check antline/ tests/
```

## 路线图

- [x] 数据源管理（添加、列出、探查）
- [x] 从 CSV 导入标准
- [x] 需求管理（创建、评估、更新）
- [x] 项目管理（创建、搭建）
- [x] 项目级 dbt 目录（`projects/PRJ-xxx/dbt/`）
- [x] 工作空间级平台配置
- [x] 基于日期的实体 ID（SRC/REQ/PRJ-YYYYMMDD-NNN）
- [x] 数据源/需求/项目的子目录结构
- [x] 交互式搭建，数据库设置提示
- [x] FDW / sync 双源模式用于行层
- [x] SQL 编译命令（不执行验证）
- [x] dbt 集成（构建、验证、交付）
- [x] **原子数据交付**（`project deliver`）：将 clean 层数据零停机交付到 prod 层，支持 atomic 重命名和 replace 两种策略，支持 dry-run 预览
- [x] 添加数据源时连接验证
- [x] 探查报告中的 PII 感知数据脱敏
- [x] 双格式报告（面向 Agent 的 YAML + 面向人的 Markdown）
- [x] 不存储凭据 —— 所有密码运行时提示
- [x] 合规审计日志
- [x] 审批时验证与探查报告的一致性
- [x] 为 IN_PROJECT 需求提供带备注的重新审批
- [x] 抽取作业（sync 模式的物理数据同步）
- [x] **智能需求评估**（`--auto`）：LLM 驱动的 5 步流水线（范围 → SQL → 审计 → 缺口填补 → 合并），直接产出模型级 SQL + 清洗规则
- [x] **本地源数据接入**（`source setup`）：支持 FDW 外联表和物理同步两种模式，在本地目标数据库中接入远程源数据
- [x] **SQL 执行校验**（`--validate`）：需求评估时自动校验生成的 SQL 语法和字段，需在本地接入源数据后使用
- [ ] Schedule 命令（cron 包装 / Airflow DAG 生成）
- [ ] 自定义 ETL 后端插件系统
- [ ] Web UI（轻量）

## 许可

Apache-2.0
