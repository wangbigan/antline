"""Tests for Antline data models."""

from antline.core.models import DataSource, DataSourceType, Requirement, RequirementStatus


def test_datasource_connection_string_postgresql():
    src = DataSource(
        id="SRC-001",
        name="test",
        db_type=DataSourceType.POSTGRESQL,
        host="localhost",
        port=5432,
        database="mydb",
        user="user",
    )
    assert src.connection_string("pass") == "postgresql+psycopg2://user:pass@localhost:5432/mydb"


def test_datasource_connection_string_mysql():
    src = DataSource(
        id="SRC-002",
        name="test",
        db_type=DataSourceType.MYSQL,
        host="localhost",
        port=3306,
        database="mydb",
        user="user",
    )
    assert src.connection_string("pass") == "mysql+pymysql://user:pass@localhost:3306/mydb"


def test_datasource_connection_string_tidb():
    src = DataSource(
        id="SRC-003",
        name="test",
        db_type=DataSourceType.TIDB,
        host="localhost",
        port=4000,
        database="mydb",
        user="user",
    )
    assert src.connection_string("pass") == "mysql+pymysql://user:pass@localhost:4000/mydb"


def test_requirement_default_status():
    req = Requirement(id="REQ-001", name="test")
    assert req.status == RequirementStatus.DRAFT
