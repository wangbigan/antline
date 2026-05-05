"""Import target schema from CSV files."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from antline.core.models import TargetSchema


def parse_field_type(field_type: str) -> tuple[str, bool]:
    """Parse 'VARCHAR(40) NOT NULL' into (data_type, nullable)."""
    field_type = field_type.strip()
    nullable = "NOT NULL" not in field_type.upper()
    # Remove NOT NULL marker
    clean = field_type.replace("NOT NULL", "").replace("not null", "").strip()
    return clean, nullable


def import_schema_from_csv(csv_path: Path) -> list[TargetSchema]:
    """Import target schema definitions from a CSV file.

    Expected CSV columns:
        module, table_name, table_comment, field_name, field_type, field_comment, example
    """
    schemas: dict[str, dict[str, Any]] = {}

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            table = row["table_name"].strip()
            module = row["module"].strip()
            table_comment = row.get("table_comment", "").strip()

            if table not in schemas:
                schemas[table] = {
                    "table": table,
                    "module": module,
                    "description": table_comment,
                    "fields": [],
                }

            field_name = row["field_name"].strip()
            field_type_raw = row.get("field_type", "").strip()
            field_comment = row.get("field_comment", "").strip()

            data_type, nullable = parse_field_type(field_type_raw)

            schemas[table]["fields"].append(
                {
                    "name": field_name,
                    "data_type": data_type,
                    "nullable": nullable,
                    "description": field_comment,
                }
            )

    return [
        TargetSchema.model_validate(
            {
                "table": s["table"],
                "description": s["description"],
                "fields": s["fields"],
            }
        )
        for s in schemas.values()
    ]


def save_schemas_as_yaml(schemas: list[TargetSchema], output_dir: Path) -> list[Path]:
    """Save schema definitions as individual YAML files."""
    import yaml

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for schema in schemas:
        path = output_dir / f"{schema.table}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                schema.model_dump(mode="json"),
                f,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        paths.append(path)

    return paths
