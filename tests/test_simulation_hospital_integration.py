"""Simulation test: hospital data integration with HIS + EMR sources.

Scenario:
    A hospital wants to integrate data from two systems:
    - HIS (Hospital Information System): patients, admissions, transfers
    - EMR (Electronic Medical Record): diagnoses, medications

    Target: Build unified patient dimension and admission tables
    (MIMIC-IV style standard).

Key challenges simulated:
    1. Cross-system patient ID mapping (HIS uses int, EMR uses string prefix)
    2. Multiple source tables per requirement (patients from HIS + EMR)
    3. Some target fields only exist in one source (missing fields like dod)
    4. Null values in real-world data
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from antline.cli import app
from antline.core import db as db_module

runner = CliRunner()

# ---------------------------------------------------------------------------
# Data generators
# ---------------------------------------------------------------------------

GENDERS = ["M", "F"]
ADMISSION_TYPES = ["EW EMER.", "ELECTIVE", "URGENT", "DIRECT EMER."]
DISCHARGE_LOCATIONS = ["HOME", "SNF", "REHAB", "DIED", "HOSPICE"]
ICD_CODES = ["I10", "E119", "J449", "N179", "Z51.11", "I25.10", "F32.9"]
DRUGS = ["Aspirin", "Metformin", "Lisinopril", "Atorvastatin", "Furosemide"]
ROUTES = ["PO", "IV", "IM", "SC"]


def _random_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def _generate_his_db(db_path: Path, n_patients: int = 100, n_admissions: int = 300) -> None:
    """Generate HIS database with patients, admissions, transfers."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # MIMIC-IV aligned field names for assess matching
        conn.execute(
            text("""
            CREATE TABLE patient_info (
                subject_id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                gender VARCHAR(1),
                dob DATE,
                phone VARCHAR(20),
                address TEXT
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE admission_records (
                hadm_id INTEGER PRIMARY KEY,
                subject_id INTEGER NOT NULL,
                admittime TIMESTAMP NOT NULL,
                dischtime TIMESTAMP,
                admission_type VARCHAR(40) NOT NULL,
                discharge_location VARCHAR(60),
                hospital_expire_flag SMALLINT
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE transfer_logs (
                transfer_id INTEGER PRIMARY KEY,
                subject_id INTEGER NOT NULL,
                eventtype VARCHAR(20),
                careunit VARCHAR(100),
                intime TIMESTAMP,
                outtime TIMESTAMP
            )
        """)
        )

        patients = []
        for i in range(1, n_patients + 1):
            name = f"Patient_{i:04d}"
            gender = random.choice(GENDERS)
            dob = _random_date(datetime(1940, 1, 1), datetime(2020, 1, 1)).strftime("%Y-%m-%d")
            phone = f"138{random.randint(10000000, 99999999)}"
            address = f"Street {random.randint(1, 999)}, City"
            patients.append((i, name, gender, dob, phone, address))

        conn.execute(
            text("INSERT INTO patient_info VALUES (:a, :b, :c, :d, :e, :f)"),
            [{"a": p[0], "b": p[1], "c": p[2], "d": p[3], "e": p[4], "f": p[5]} for p in patients],
        )

        admissions = []
        for i in range(1, n_admissions + 1):
            pid = random.randint(1, n_patients)
            adm = _random_date(datetime(2020, 1, 1), datetime(2024, 1, 1))
            disch = adm + timedelta(days=random.randint(1, 30))
            adm_type = random.choice(ADMISSION_TYPES)
            disch_loc = random.choice(DISCHARGE_LOCATIONS)
            expire = 1 if disch_loc == "DIED" else 0
            admissions.append(
                (
                    i,
                    pid,
                    adm.strftime("%Y-%m-%d %H:%M:%S"),
                    disch.strftime("%Y-%m-%d %H:%M:%S"),
                    adm_type,
                    disch_loc,
                    expire,
                )
            )

        conn.execute(
            text("INSERT INTO admission_records VALUES (:a, :b, :c, :d, :e, :f, :g)"),
            [
                {"a": a[0], "b": a[1], "c": a[2], "d": a[3], "e": a[4], "f": a[5], "g": a[6]}
                for a in admissions
            ],
        )

        transfers = []
        for i in range(1, 50):
            pid = random.randint(1, n_patients)
            event = random.choice(["transfer", "admit", "discharge"])
            unit = random.choice(["MICU", "SICU", "CCU", "GENERAL WARD"])
            intime = _random_date(datetime(2020, 1, 1), datetime(2024, 1, 1))
            outtime = intime + timedelta(days=random.randint(1, 7))
            transfers.append(
                (
                    i,
                    pid,
                    event,
                    unit,
                    intime.strftime("%Y-%m-%d %H:%M:%S"),
                    outtime.strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

        conn.execute(
            text("INSERT INTO transfer_logs VALUES (:a, :b, :c, :d, :e, :f)"),
            [{"a": t[0], "b": t[1], "c": t[2], "d": t[3], "e": t[4], "f": t[5]} for t in transfers],
        )

        conn.commit()


def _generate_emr_db(db_path: Path, n_patients: int = 100, n_diagnoses: int = 500) -> None:
    """Generate EMR database with diagnoses and medications.

    Patient IDs use string prefix to simulate cross-system ID mismatch.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE diagnoses (
                diagnosis_id INTEGER PRIMARY KEY,
                patient_mrn VARCHAR(20) NOT NULL,
                icd_code VARCHAR(10),
                icd_description TEXT,
                diagnosis_date DATE
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE medication_orders (
                order_id INTEGER PRIMARY KEY,
                patient_mrn VARCHAR(20) NOT NULL,
                drug_name VARCHAR(100),
                dose VARCHAR(50),
                route VARCHAR(20),
                start_time TIMESTAMP,
                end_time TIMESTAMP
            )
        """)
        )

        # HIS patient_id = integer 1..100
        # EMR patient_mrn = "MRN-001" .. "MRN-100"
        diagnoses = []
        for i in range(1, n_diagnoses + 1):
            pid_num = random.randint(1, n_patients)
            mrn = f"MRN-{pid_num:03d}"
            icd = random.choice(ICD_CODES)
            desc = f"Diagnosis for {icd}"
            diag_date = _random_date(datetime(2020, 1, 1), datetime(2024, 1, 1)).strftime(
                "%Y-%m-%d"
            )
            diagnoses.append((i, mrn, icd, desc, diag_date))

        conn.execute(
            text("INSERT INTO diagnoses VALUES (:a, :b, :c, :d, :e)"),
            [{"a": d[0], "b": d[1], "c": d[2], "d": d[3], "e": d[4]} for d in diagnoses],
        )

        medications = []
        for i in range(1, 800):
            pid_num = random.randint(1, n_patients)
            mrn = f"MRN-{pid_num:03d}"
            drug = random.choice(DRUGS)
            dose = f"{random.randint(1, 500)}mg"
            route = random.choice(ROUTES)
            start = _random_date(datetime(2020, 1, 1), datetime(2024, 1, 1))
            end = start + timedelta(days=random.randint(1, 14))
            medications.append(
                (
                    i,
                    mrn,
                    drug,
                    dose,
                    route,
                    start.strftime("%Y-%m-%d %H:%M:%S"),
                    end.strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

        conn.execute(
            text("INSERT INTO medication_orders VALUES (:a, :b, :c, :d, :e, :f, :g)"),
            [
                {"a": m[0], "b": m[1], "c": m[2], "d": m[3], "e": m[4], "f": m[5], "g": m[6]}
                for m in medications
            ],
        )

        conn.commit()


# ---------------------------------------------------------------------------
# Target schema (MIMIC-IV style, inline for test independence)
# ---------------------------------------------------------------------------

TARGET_SCHEMA_PATIENTS = {
    "table": "patients",
    "description": "患者维度表",
    "fields": [
        {
            "name": "subject_id",
            "data_type": "INTEGER",
            "nullable": False,
            "description": "患者唯一标识符",
        },
        {"name": "name", "data_type": "VARCHAR(100)", "nullable": False, "description": "患者姓名"},
        {"name": "gender", "data_type": "VARCHAR(1)", "nullable": True, "description": "性别"},
        {"name": "dob", "data_type": "DATE", "nullable": True, "description": "出生日期"},
        {"name": "dod", "data_type": "DATE", "nullable": True, "description": "死亡日期"},
    ],
}

TARGET_SCHEMA_ADMISSIONS = {
    "table": "admissions",
    "description": "入院记录表",
    "fields": [
        {"name": "subject_id", "data_type": "INTEGER", "nullable": False, "description": "患者ID"},
        {"name": "hadm_id", "data_type": "INTEGER", "nullable": False, "description": "住院记录ID"},
        {
            "name": "admittime",
            "data_type": "TIMESTAMP",
            "nullable": False,
            "description": "入院时间",
        },
        {
            "name": "dischtime",
            "data_type": "TIMESTAMP",
            "nullable": True,
            "description": "出院时间",
        },
        {
            "name": "admission_type",
            "data_type": "VARCHAR(40)",
            "nullable": False,
            "description": "入院类型",
        },
        {
            "name": "discharge_location",
            "data_type": "VARCHAR(60)",
            "nullable": True,
            "description": "出院去向",
        },
        {
            "name": "hospital_expire_flag",
            "data_type": "SMALLINT",
            "nullable": True,
            "description": "院内死亡标志",
        },
    ],
}


@pytest.fixture
def hospital_dbs(monkeypatch):
    """Create two simulated hospital databases (HIS + EMR)."""
    with tempfile.TemporaryDirectory() as tmp:
        his_db = Path(tmp) / "his.db"
        emr_db = Path(tmp) / "emr.db"

        _generate_his_db(his_db, n_patients=100, n_admissions=300)
        _generate_emr_db(emr_db, n_patients=100, n_diagnoses=500)

        yield {"his": his_db, "emr": emr_db, "tmp": Path(tmp)}


def test_hospital_integration_workflow(hospital_dbs: dict, monkeypatch) -> None:
    """Full integration workflow with simulated hospital data."""
    tmp = hospital_dbs["tmp"]
    project_root = tmp / "antline_project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    # --- Step 1: init ---
    result = runner.invoke(app, ["init", "--path", ".", "--name", "hospital-integration"])
    assert result.exit_code == 0, result.output

    # --- Step 2: add HIS source ---
    result = runner.invoke(
        app,
        [
            "source",
            "add",
            "--type",
            "postgresql",
            "--host",
            "localhost",
            "--database",
            str(hospital_dbs["his"]),
            "--user",
            "wbg",
            "--password",
            "5678",
            "--name",
            "HIS",
            "--no-test-connection",
        ],
    )
    assert result.exit_code == 0, result.output
    his_id = "SRC-001"

    # --- Step 3: add EMR source ---
    result = runner.invoke(
        app,
        [
            "source",
            "add",
            "--type",
            "postgresql",
            "--host",
            "localhost",
            "--database",
            str(hospital_dbs["emr"]),
            "--user",
            "wbg",
            "--password",
            "5678",
            "--name",
            "EMR",
            "--no-test-connection",
        ],
    )
    assert result.exit_code == 0, result.output
    emr_id = "SRC-002"

    # --- Step 4: explore both sources ---
    his_engine = create_engine(f"sqlite:///{hospital_dbs['his']}")
    emr_engine = create_engine(f"sqlite:///{hospital_dbs['emr']}")

    def mock_get_engine(source):
        if source.id == his_id:
            return his_engine
        return emr_engine

    monkeypatch.setattr(db_module, "get_engine", mock_get_engine)

    for sid in [his_id, emr_id]:
        result = runner.invoke(app, ["source", "explore", sid])
        assert result.exit_code == 0, result.output
        assert (project_root / "reports" / f"{sid}_explore.yml").exists()

    # Verify explore reports contain expected tables
    his_report = yaml.safe_load((project_root / "reports" / f"{his_id}_explore.yml").read_text())
    his_tables = {t["name"] for t in his_report["tables"]}
    assert "patient_info" in his_tables
    assert "admission_records" in his_tables
    assert his_report["summary"]["total_tables"] == 3

    emr_report = yaml.safe_load((project_root / "reports" / f"{emr_id}_explore.yml").read_text())
    emr_tables = {t["name"] for t in emr_report["tables"]}
    assert "diagnoses" in emr_tables
    assert "medication_orders" in emr_tables

    # --- Step 5: create requirements with target schemas ---
    schema_dir = project_root / "target_schema"
    schema_dir.mkdir(parents=True, exist_ok=True)

    for schema in [TARGET_SCHEMA_PATIENTS, TARGET_SCHEMA_ADMISSIONS]:
        path = schema_dir / f"{schema['table']}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(schema, f, allow_unicode=True)

    result = runner.invoke(
        app,
        [
            "requirement",
            "create",
            "--name",
            "患者维度表",
            "--target-schema",
            str(schema_dir / "patients.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output
    req_patients = "REQ-001"

    result = runner.invoke(
        app,
        [
            "requirement",
            "create",
            "--name",
            "入院记录表",
            "--target-schema",
            str(schema_dir / "admissions.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output
    req_admissions = "REQ-002"

    # --- Step 6: assess (generates prompts/template) + simulate completion + approve ---
    for req_id in [req_patients, req_admissions]:
        result = runner.invoke(app, ["requirement", "assess", req_id, his_id, emr_id])
        assert result.exit_code == 0, result.output

        # Simulate LLM generating assessment from template (Markdown + frontmatter)
        template_path = project_root / "reports" / "assessment" / f"{req_id}_template.md"
        template = yaml.safe_load(
            template_path.read_text().split("---")[1]
        )
        template["feasible"] = True
        for m in template["field_mappings"]:
            target = m["target_field"]
            if target == "patients.subject_id":
                m.update({"source_table": "patient_info", "source_field": "subject_id", "mapping_type": "direct", "risk": "low"})
            elif target == "patients.name":
                m.update({"source_table": "patient_info", "source_field": "name", "mapping_type": "direct", "risk": "low"})
            elif target == "patients.gender":
                m.update({"source_table": "patient_info", "source_field": "gender", "mapping_type": "direct", "risk": "low"})
            elif target == "patients.dob":
                m.update({"source_table": "patient_info", "source_field": "dob", "mapping_type": "direct", "risk": "low"})
            elif target == "patients.dod":
                m.update({"mapping_type": "missing", "risk": "high"})
            elif target == "admissions.subject_id":
                m.update({"source_table": "admission_records", "source_field": "subject_id", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.hadm_id":
                m.update({"source_table": "admission_records", "source_field": "hadm_id", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.admittime":
                m.update({"source_table": "admission_records", "source_field": "admittime", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.dischtime":
                m.update({"source_table": "admission_records", "source_field": "dischtime", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.admission_type":
                m.update({"source_table": "admission_records", "source_field": "admission_type", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.discharge_location":
                m.update({"source_table": "admission_records", "source_field": "discharge_location", "mapping_type": "direct", "risk": "low"})
            elif target == "admissions.hospital_expire_flag":
                m.update({"source_table": "admission_records", "source_field": "hospital_expire_flag", "mapping_type": "direct", "risk": "low"})

        # Build Markdown assessment with frontmatter
        frontmatter = yaml.safe_dump(template, allow_unicode=True, sort_keys=False)
        assessment_md = f"""---
{frontmatter.rstrip()}
---

# 可行性评估报告：{req_id}

## 结论

可行性：是

## 字段映射

| 目标字段 | 源表 | 源字段 | 映射类型 | 风险 |
|----------|------|--------|----------|------|
| ... | ... | ... | ... | ... |

## 风险分析

无重大风险

## 备注

测试评估报告
"""
        assessment_path = project_root / "reports" / "assessment" / f"{req_id}_assessment.md"
        with open(assessment_path, "w", encoding="utf-8") as f:
            f.write(assessment_md)

        result = runner.invoke(
            app,
            ["requirement", "approve", req_id, "--file", str(assessment_path)],
        )
        assert result.exit_code == 0, result.output

    # Verify patient requirement mappings
    req_data = yaml.safe_load((project_root / "requirements" / f"{req_patients}.yml").read_text())
    assert req_data["status"] == "approved"
    mappings = {m["target_field"]: m for m in req_data["assessment"]["field_mappings"]}

    # HIS has subject_id, name, gender, dob (MIMIC-aligned)
    assert mappings["patients.subject_id"]["mapping_type"] == "direct"
    assert mappings["patients.name"]["mapping_type"] == "direct"
    assert mappings["patients.gender"]["mapping_type"] == "direct"
    assert mappings["patients.dob"]["mapping_type"] == "direct"
    # dod is missing (not in either source)
    assert mappings["patients.dod"]["mapping_type"] == "missing"

    # Verify admission requirement mappings
    req_data = yaml.safe_load((project_root / "requirements" / f"{req_admissions}.yml").read_text())
    mappings = {m["target_field"]: m for m in req_data["assessment"]["field_mappings"]}
    assert mappings["admissions.subject_id"]["mapping_type"] == "direct"
    assert mappings["admissions.hadm_id"]["mapping_type"] == "direct"
    assert mappings["admissions.admittime"]["mapping_type"] == "direct"
    assert mappings["admissions.dischtime"]["mapping_type"] == "direct"

    # --- Step 7: create project ---
    result = runner.invoke(
        app,
        [
            "project",
            "create",
            "--name",
            "医院数据集成项目",
            "--requirement",
            req_patients,
            "--requirement",
            req_admissions,
        ],
    )
    assert result.exit_code == 0, result.output
    prj_id = "PRJ-001"

    prj_data = yaml.safe_load((project_root / "projects" / f"{prj_id}.yml").read_text())
    assert prj_data["name"] == "医院数据集成项目"
    assert set(prj_data["requirement_ids"]) == {req_patients, req_admissions}
    assert len(prj_data["qc_rules"]) == 2  # one per target table

    # --- Step 8: scaffold dbt project ---
    result = runner.invoke(
        app,
        [
            "project",
            "scaffold",
            prj_id,
            "--db-type",
            "postgresql",
            "--host",
            "localhost",
            "--port",
            "5432",
            "--user",
            "postgres",
            "--password",
            "test",
            "--skip-db-setup",
        ],
    )
    assert result.exit_code == 0, result.output

    dbt_dir = project_root / "dbt" / prj_id
    assert (dbt_dir / "dbt_project.yml").exists()
    assert (dbt_dir / "models" / "map" / "map_patients.sql").exists()
    assert (dbt_dir / "models" / "map" / "map_admissions.sql").exists()

    # Verify map model for patients has correct field references
    map_patients = (dbt_dir / "models" / "map" / "map_patients.sql").read_text()
    assert "subject_id" in map_patients
    assert "name" in map_patients
    assert "NULL AS dod" in map_patients  # missing field

    # Verify sources.yml only includes HIS (EMR tables not referenced in mappings)
    sources_yml = yaml.safe_load((dbt_dir / "models" / "sources.yml").read_text())
    source_names = {s["name"] for s in sources_yml["sources"]}
    assert his_id in source_names
    # EMR not included because no fields map to EMR tables in this assessment

    # --- Step 9: verify row models exist for referenced tables ---
    row_dir = dbt_dir / "models" / "row"
    row_models = list(row_dir.glob("*.sql"))
    # HIS has patient_info and admission_records referenced
    assert len(row_models) == 2
    row_model_names = {f.stem for f in row_models}
    assert "row_patient_info" in row_model_names
    assert "row_admission_records" in row_model_names
