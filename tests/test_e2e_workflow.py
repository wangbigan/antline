"""End-to-end workflow test: init → source → explore → requirement → assess → project → scaffold."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from antline.cli import app
from antline.core import db as db_module

runner = CliRunner()

TODAY = date.today().strftime("%Y%m%d")


@pytest.fixture
def sqlite_db():
    """Create a file-based SQLite database with sample tables."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(
            text("""
                CREATE TABLE patients (
                    patient_id INTEGER PRIMARY KEY,
                    patient_name TEXT NOT NULL,
                    patient_age INTEGER,
                    gender TEXT
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO patients (patient_id, patient_name, patient_age, gender)
                VALUES (1, 'Alice', 30, 'F'), (2, 'Bob', 45, 'M')
            """)
        )
        conn.commit()
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def target_schema_yaml():
    """Create a target schema YAML file."""
    return {
        "table": "dim_patients",
        "description": "患者维度表",
        "fields": [
            {
                "name": "patient_id",
                "data_type": "INTEGER",
                "nullable": False,
                "description": "患者ID",
            },
            {
                "name": "patient_name",
                "data_type": "VARCHAR(100)",
                "nullable": False,
                "description": "患者姓名",
            },
            {
                "name": "patient_age",
                "data_type": "INTEGER",
                "nullable": True,
                "description": "患者年龄",
            },
            {
                "name": "missing_field",
                "data_type": "VARCHAR(50)",
                "nullable": True,
                "description": "不存在的字段",
            },
        ],
    }


def test_full_workflow(sqlite_db: Path, target_schema_yaml: dict, monkeypatch) -> None:
    """Run the complete Antline workflow end-to-end."""
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        monkeypatch.chdir(project_root)

        # --- Step 1: init ---
        result = runner.invoke(
            app,
            [
                "init",
                "--path",
                ".",
                "--name",
                "e2e-test",
                "--password",
                "test",
                "--no-test-connection",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (project_root / "antline.yml").exists()

        # --- Step 2: source add ---
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
                str(sqlite_db),
                "--user",
                "wbg",
                "--password",
                "5678",
                "--no-test-connection",
            ],
        )
        assert result.exit_code == 0, result.output
        src_id = f"SRC-{TODAY}-001"
        assert (project_root / "sources" / src_id / "source.yml").exists()

        # --- Step 3: source explore (mock engine to use SQLite) ---
        engine = create_engine(f"sqlite:///{sqlite_db}")
        monkeypatch.setattr(db_module, "get_engine", lambda _source: engine)

        result = runner.invoke(app, ["source", "explore", src_id])
        assert result.exit_code == 0, result.output
        assert (project_root / "sources" / src_id / "explore" / "report.yml").exists()

        # --- Step 4: requirement create with target schema ---
        schema_path = project_root / "target_schema" / "dim_patients.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        with open(schema_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(target_schema_yaml, f, allow_unicode=True)

        result = runner.invoke(
            app,
            [
                "requirement",
                "create",
                "--name",
                "患者维度表",
                "--target-schema",
                str(schema_path),
            ],
        )
        assert result.exit_code == 0, result.output
        req_id = f"REQ-{TODAY}-001"
        assert (project_root / "requirements" / req_id / "requirement.yml").exists()

        # Verify requirement loaded the schema
        req_data = yaml.safe_load(
            (project_root / "requirements" / req_id / "requirement.yml").read_text()
        )
        assert req_data["target_schemas"][0]["table"] == "dim_patients"
        assert len(req_data["target_schemas"][0]["fields"]) == 4

        # --- Step 5: requirement assess (generates prompts/template) ---
        result = runner.invoke(app, ["requirement", "assess", req_id, src_id])
        assert result.exit_code == 0, result.output

        # Verify prompt/template files generated
        assessment_dir = project_root / "requirements" / req_id / "assessment"
        assert (assessment_dir / "prompt.md").exists()
        assert (assessment_dir / "guide.md").exists()
        assert (assessment_dir / "template.md").exists()

        # Simulate LLM generating assessment from template (Markdown + frontmatter)
        template = yaml.safe_load((assessment_dir / "template.md").read_text().split("---")[1])
        template["feasible"] = True
        for m in template["field_mappings"]:
            if m["target_field"] == "dim_patients.patient_id":
                m["source_table"] = "patients"
                m["source_field"] = "patient_id"
                m["mapping_type"] = "direct"
                m["risk"] = "low"
            elif m["target_field"] == "dim_patients.patient_name":
                m["source_table"] = "patients"
                m["source_field"] = "patient_name"
                m["mapping_type"] = "direct"
                m["risk"] = "low"
            elif m["target_field"] == "dim_patients.patient_age":
                m["source_table"] = "patients"
                m["source_field"] = "patient_age"
                m["mapping_type"] = "direct"
                m["risk"] = "low"
            elif m["target_field"] == "dim_patients.missing_field":
                m["mapping_type"] = "missing"
                m["risk"] = "high"

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
| dim_patients.patient_id | patients | patient_id | direct | low |
| dim_patients.patient_name | patients | patient_name | direct | low |
| dim_patients.patient_age | patients | patient_age | direct | low |
| dim_patients.missing_field | - | - | missing | high |

## 风险分析

无重大风险

## 备注

测试评估报告
"""
        assessment_path = assessment_dir / "assessment.md"
        with open(assessment_path, "w", encoding="utf-8") as f:
            f.write(assessment_md)

        # --- Step 5b: approve with completed assessment file ---
        result = runner.invoke(
            app,
            [
                "requirement",
                "approve",
                req_id,
                "--file",
                str(assessment_path),
            ],
        )
        assert result.exit_code == 0, result.output

        # Verify assessment stored in requirement
        req_data = yaml.safe_load(
            (project_root / "requirements" / req_id / "requirement.yml").read_text()
        )
        assert req_data["status"] == "approved"
        assert req_data["assessment"]["feasible"] is True
        assert len(req_data["assessment"]["field_mappings"]) == 4

        # Check mappings
        mappings = {m["target_field"]: m for m in req_data["assessment"]["field_mappings"]}
        assert mappings["dim_patients.patient_id"]["mapping_type"] == "direct"
        assert mappings["dim_patients.patient_name"]["mapping_type"] == "direct"
        assert mappings["dim_patients.patient_age"]["mapping_type"] == "direct"
        assert mappings["dim_patients.missing_field"]["mapping_type"] == "missing"

        # --- Step 6: project create ---
        result = runner.invoke(
            app,
            [
                "project",
                "create",
                "--name",
                "患者数据项目",
                "--requirement",
                req_id,
            ],
        )
        assert result.exit_code == 0, result.output
        prj_id = f"PRJ-{TODAY}-001"
        assert (project_root / "projects" / prj_id / "project.yml").exists()

        # Verify project references requirement
        prj_data = yaml.safe_load((project_root / "projects" / prj_id / "project.yml").read_text())
        assert prj_data["name"] == "患者数据项目"
        assert req_id in prj_data["requirement_ids"]
        assert len(prj_data["qc_rules"]) > 0

        # --- Step 7: project scaffold ---
        result = runner.invoke(
            app,
            [
                "project",
                "scaffold",
                prj_id,
                "--skip-db-setup",
            ],
        )
        assert result.exit_code == 0, result.output

        # Verify dbt project structure (per-project dbt dir)
        dbt_dir = project_root / "projects" / prj_id / "dbt"
        assert (dbt_dir / "dbt_project.yml").exists()
        assert (dbt_dir / "profiles.yml").exists()
        assert (dbt_dir / "models" / "row").exists()
        assert (dbt_dir / "models" / "map").exists()
        assert (dbt_dir / "models" / "clean").exists()
        assert (dbt_dir / "models" / "sources.yml").exists()

        # Verify map model references target fields
        map_model = dbt_dir / "models" / "map" / "map_dim_patients.sql"
        assert map_model.exists()
        map_content = map_model.read_text()
        assert "patient_id" in map_content
        assert "patient_name" in map_content
