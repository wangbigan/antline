"""End-to-end tests for schema import → requirement workflow."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest
import yaml

from antline.core.csv_schema import import_schema_from_csv, save_schemas_as_yaml
from antline.core.models import TargetSchema


@pytest.fixture
def sample_csv():
    """Create a temporary CSV file with sample schema definitions."""
    path = Path(tempfile.mktemp(suffix=".csv"))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "module",
                "table_name",
                "table_comment",
                "field_name",
                "field_type",
                "field_comment",
                "example",
            ]
        )
        writer.writerow(
            [
                "Hosp",
                "admissions",
                "入院记录",
                "subject_id",
                "INTEGER NOT NULL",
                "患者ID",
                "1",
            ]
        )
        writer.writerow(
            [
                "Hosp",
                "admissions",
                "入院记录",
                "admittime",
                "TIMESTAMP NOT NULL",
                "入院时间",
                "2024-01-01",
            ]
        )
        writer.writerow(
            [
                "Hosp",
                "patients",
                "患者信息",
                "subject_id",
                "INTEGER NOT NULL",
                "患者ID",
                "1",
            ]
        )
    yield path
    path.unlink(missing_ok=True)


def test_parse_field_type():
    """Test field type parsing with NOT NULL detection."""
    from antline.core.csv_schema import parse_field_type

    assert parse_field_type("INTEGER NOT NULL") == ("INTEGER", False)
    assert parse_field_type("VARCHAR(40) NOT NULL") == ("VARCHAR(40)", False)
    assert parse_field_type("TIMESTAMP") == ("TIMESTAMP", True)
    assert parse_field_type("  SMALLINT  ") == ("SMALLINT", True)


def test_import_schema_from_csv(sample_csv: Path):
    """Test CSV import produces correct schema objects."""
    schemas = import_schema_from_csv(sample_csv)

    assert len(schemas) == 2

    admissions = next(s for s in schemas if s.table == "admissions")
    assert admissions.description == "入院记录"
    assert len(admissions.fields) == 2

    sid = next(f for f in admissions.fields if f.name == "subject_id")
    assert sid.data_type == "INTEGER"
    assert sid.nullable is False
    assert sid.description == "患者ID"

    admittime = next(f for f in admissions.fields if f.name == "admittime")
    assert admittime.data_type == "TIMESTAMP"
    assert admittime.nullable is False

    patients = next(s for s in schemas if s.table == "patients")
    assert patients.description == "患者信息"
    assert len(patients.fields) == 1


def test_save_schemas_as_yaml(tmp_path: Path):
    """Test saving schemas produces valid YAML files."""
    schemas = [
        TargetSchema(
            table="test_table",
            description="Test",
            fields=[
                {"name": "id", "data_type": "INTEGER", "nullable": False, "description": "PK"},
                {"name": "name", "data_type": "VARCHAR(100)", "nullable": True, "description": ""},
            ],
        )
    ]

    paths = save_schemas_as_yaml(schemas, tmp_path)
    assert len(paths) == 1

    data = yaml.safe_load(paths[0].read_text())
    assert data["table"] == "test_table"
    assert len(data["fields"]) == 2
    assert data["fields"][0]["nullable"] is False


def test_schema_yaml_roundtrip(sample_csv: Path, tmp_path: Path):
    """Test full roundtrip: CSV → schemas → YAML → loaded back."""
    schemas = import_schema_from_csv(sample_csv)
    paths = save_schemas_as_yaml(schemas, tmp_path)

    for path in paths:
        data = yaml.safe_load(path.read_text())
        loaded = TargetSchema.model_validate(data)
        assert loaded.table == data["table"]
        assert len(loaded.fields) == len(data["fields"])
