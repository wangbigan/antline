"""Pydantic models for Antline entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataSourceType(str, Enum):
    """Supported database types."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    TIDB = "tidb"


class WorkspacePlatformConfig(BaseModel):
    """Target database platform configuration at workspace level.

    Passwords are NEVER stored here. They must be provided at runtime.
    """

    model_config = ConfigDict(populate_by_name=True)

    db_type: DataSourceType
    host: str
    port: int
    user: str


class FieldStats(BaseModel):
    """Statistics for a single column."""

    model_config = ConfigDict(populate_by_name=True)

    null_count: int = 0
    null_rate: float = 0.0
    unique_count: int = 0
    is_unique_candidate: bool = False
    min_value: str | None = None
    max_value: str | None = None
    topn_values: list[dict[str, Any]] = Field(default_factory=list)


class ColumnMeta(BaseModel):
    """Column metadata from database reflection."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None
    max_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    comment: str | None = None
    stats: FieldStats = Field(default_factory=FieldStats)
    sample_data: list[Any] = Field(default_factory=list)


class TableMeta(BaseModel):
    """Table metadata from database reflection."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_name: str | None = Field(default=None, alias="schema")
    row_count: int = 0
    comment: str | None = None
    columns: list[ColumnMeta] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)


class DataSource(BaseModel):
    """A configured data source.

    Passwords are NEVER stored. They must be provided at runtime.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    db_type: DataSourceType
    host: str
    port: int
    database: str
    user: str
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))

    def connection_string(self, password: str = "") -> str:
        """Build SQLAlchemy connection string (password provided at runtime)."""
        if self.db_type in (DataSourceType.MYSQL, DataSourceType.TIDB):
            return f"mysql+pymysql://{self.user}:{password}@{self.host}:{self.port}/{self.database}"
        return f"postgresql+psycopg2://{self.user}:{password}@{self.host}:{self.port}/{self.database}"


class SourceExploreReport(BaseModel):
    """Output of `source explore`."""

    model_config = ConfigDict(populate_by_name=True)

    source_id: str
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone(timedelta(hours=8)))
    )
    tables: list[TableMeta] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class RequirementStatus(str, Enum):
    """Requirement lifecycle states."""

    DRAFT = "draft"
    ASSESSED = "assessed"
    APPROVED = "approved"
    IN_PROJECT = "in_project"


class TargetField(BaseModel):
    """Field definition in target schema."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    data_type: str
    nullable: bool = True
    description: str = ""
    constraints: list[str] = Field(default_factory=list)


class TargetSchema(BaseModel):
    """Target data standard for a requirement."""

    model_config = ConfigDict(populate_by_name=True)

    table: str
    description: str = ""
    fields: list[TargetField] = Field(default_factory=list)


class AssessmentRisk(BaseModel):
    """Risk identified during requirement assessment."""

    model_config = ConfigDict(populate_by_name=True)

    level: Literal["low", "medium", "high", "critical"] = "low"
    description: str = ""
    source_table: str | None = None
    target_field: str | None = None


class FieldMapping(BaseModel):
    """Mapping from source to target field."""

    model_config = ConfigDict(populate_by_name=True)

    target_field: str
    source_field: str | None = None
    source_table: str | None = None
    mapping_type: Literal["direct", "transform", "missing", "merge"] = "direct"
    transform_logic: str = ""
    transform_sql: str = ""            # 模型级SQL表达式或字段级转换片段
    confidence: float = 0.0            # 匹配置信度 0.0-1.0
    risk: Literal["low", "medium", "high", "critical"] = "low"
    source_meta: dict[str, Any] = Field(default_factory=dict)


class CleanRule(BaseModel):
    """数据清洗规则，直接指导 clean 层 SQL 生成。"""

    model_config = ConfigDict(populate_by_name=True)

    target_field: str
    rules: list[Literal[
        "cast_type",
        "coalesce_null",
        "trim_whitespace",
        "uppercase",
        "lowercase",
        "deduplicate",
        "standardize_date",
    ]] = Field(default_factory=list)
    cast_target_type: str = ""
    coalesce_default: str = ""


class RequirementAssessment(BaseModel):
    """Result of assessing a requirement against data sources."""

    model_config = ConfigDict(populate_by_name=True)

    feasible: bool = False
    report_path: str = ""
    source_ids: list[str] = Field(default_factory=list)
    field_mappings: list[FieldMapping] = Field(default_factory=list)
    clean_rules: list[CleanRule] = Field(default_factory=list)
    risks: list[AssessmentRisk] = Field(default_factory=list)
    notes: str = ""
    reapproval_reason: str = ""
    assessed_at: datetime | None = None
    engine_version: str = "2.0-llm"
    auto_assessed: bool = False
    model_sqls: dict[str, str] = Field(default_factory=dict)
    source_scope: dict[str, Any] = Field(default_factory=dict)


class Requirement(BaseModel):
    """A data requirement."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    status: RequirementStatus = RequirementStatus.DRAFT
    background: str = ""  # business context / why
    goal: str = ""  # target outcome / what
    target_schemas: list[TargetSchema] = Field(default_factory=list)
    assessment: RequirementAssessment | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))


class ProjectStatus(str, Enum):
    """Project lifecycle states."""

    ACTIVE = "active"
    QC_PASSED = "qc_passed"
    DELIVERED = "delivered"
    ARCHIVED = "archived"


class QCRule(BaseModel):
    """Quality control rule."""

    model_config = ConfigDict(populate_by_name=True)

    table: str
    null_checks: list[str] = Field(default_factory=list)
    unique_checks: list[str] = Field(default_factory=list)
    custom_checks: list[str] = Field(default_factory=list)


class ProjectVersion(BaseModel):
    """A built version of a project."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    dbt_manifest: str | None = None
    qc_report: str | None = None
    passed: bool = False
    notes: str = ""


class Project(BaseModel):
    """A data project (initiated from approved requirements)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    description: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    solution_draft: str = ""
    qc_rules: list[QCRule] = Field(default_factory=list)
    versions: list[ProjectVersion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
