from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from data_analysis_agent.persistence.database import Base, init_database


EXPECTED_TABLES = {
    "users",
    "datasets",
    "analysis_tasks",
    "analysis_task_datasets",
    "task_events",
    "tool_calls",
    "executions",
    "artifacts",
    "reports",
}


def test_local_schema_has_all_tables_and_task_indexes(engine):
    init_database(engine)
    inspector = inspect(engine)

    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    index_names = {item["name"] for item in inspector.get_indexes("analysis_tasks")}
    assert {"ix_analysis_tasks_status", "ix_analysis_tasks_created_at"} <= index_names
    assert "ix_analysis_tasks_status_created_at" in index_names


def test_task_idempotency_is_unique_and_task_dataset_is_composite_key(engine):
    init_database(engine)
    inspector = inspect(engine)

    unique_names = {
        item["name"] for item in inspector.get_unique_constraints("analysis_tasks")
    }
    assert "uq_analysis_tasks_user_idempotency" in unique_names
    primary_key = inspector.get_pk_constraint("analysis_task_datasets")
    assert primary_key["constrained_columns"] == ["task_id", "dataset_id"]


def test_migration_is_repeatable_and_downgrade_removes_schema(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    config = Config("alembic.ini")
    db_url = f"sqlite:///{database_path}"
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")
    migration_engine = create_engine(db_url)
    try:
        assert EXPECTED_TABLES <= set(inspect(migration_engine).get_table_names())
        command.upgrade(config, "head")
        assert EXPECTED_TABLES <= set(inspect(migration_engine).get_table_names())
        command.downgrade(config, "base")
        assert not (EXPECTED_TABLES & set(inspect(migration_engine).get_table_names()))
    finally:
        migration_engine.dispose()


def test_initial_tables_compile_for_mysql():
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert sql.startswith("\nCREATE TABLE")
