from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from data_analysis_agent.persistence.database import Base, init_database
from data_analysis_agent.persistence import orm_models  # noqa: F401
from data_analysis_agent.persistence.errors import DatabaseConfigurationError
from data_analysis_agent.persistence.orm_models import (
    AnalysisTaskORM,
    TaskEventORM,
    UserORM,
)
from data_analysis_agent.domain.enums import TaskEventType, TaskStatus


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


def test_report_composite_foreign_key_rejects_artifact_from_another_task(engine):
    init_database(engine)
    from datetime import datetime, timezone
    from uuid import uuid4

    now = datetime.now(timezone.utc)
    user_id, first_task_id, second_task_id, artifact_id = [uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(insert(UserORM).values(user_id=user_id, created_at=now))
        for task_id, key in ((first_task_id, "first"), (second_task_id, "second")):
            connection.execute(insert(AnalysisTaskORM).values(
                task_id=task_id, user_id=user_id, idempotency_key=key,
                request_hash=key, query=key, status=TaskStatus.PENDING,
                max_rounds=1, created_at=now, updated_at=now, metadata_json={},
                model_call_count=0, model_duration_ms=0,
            ))
        connection.execute(insert(Base.metadata.tables["artifacts"]).values(
            artifact_id=artifact_id, task_id=first_task_id, artifact_type="REPORT",
            name="first.md", format="MARKDOWN", size_bytes=0, metadata_json={},
            created_at=now,
        ))
        with pytest.raises(IntegrityError):
            connection.execute(insert(Base.metadata.tables["reports"]).values(
                report_id=uuid4(), artifact_id=artifact_id, task_id=second_task_id,
                format="MARKDOWN", storage_uri="s3://second", size_bytes=0,
                created_at=now,
            ))


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


def test_production_alembic_rejects_sqlite_url_without_llm_settings(
    monkeypatch, tmp_path: Path
):
    database_path = tmp_path / "production.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(DatabaseConfigurationError, match="MySQL.*DATABASE_URL|DATABASE_URL.*MySQL"):
        command.upgrade(config, "head")


def test_initial_tables_compile_for_mysql():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert sql.startswith("\nCREATE TABLE")


def test_status_changed_events_enforce_legal_transitions(engine):
    init_database(engine)
    from uuid import uuid4
    from datetime import datetime, timezone

    user_id, task_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(insert(UserORM).values(user_id=user_id, created_at=now))
        connection.execute(insert(AnalysisTaskORM).values(
            task_id=task_id, user_id=user_id, idempotency_key="k", request_hash="h",
            query="q", status=TaskStatus.PENDING, max_rounds=1, created_at=now,
            updated_at=now, metadata_json={}, model_call_count=0, model_duration_ms=0,
        ))
        connection.execute(insert(TaskEventORM).values(
            event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED,
            from_status=None, to_status=TaskStatus.PENDING, occurred_at=now, metadata_json={},
        ))
        connection.execute(insert(TaskEventORM).values(
            event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED,
            from_status=TaskStatus.PENDING, to_status=TaskStatus.QUEUED,
            occurred_at=now, metadata_json={},
        ))
        connection.execute(insert(TaskEventORM).values(
            event_id=uuid4(), task_id=task_id, event_type=TaskEventType.TOOL_CALLED,
            from_status=TaskStatus.COMPLETED, to_status=TaskStatus.RUNNING,
            occurred_at=now, metadata_json={},
        ))
        try:
            connection.execute(insert(TaskEventORM).values(
                event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED,
                from_status=TaskStatus.COMPLETED, to_status=TaskStatus.RUNNING,
                occurred_at=now, metadata_json={},
            ))
        except IntegrityError:
            pass
        else:
            raise AssertionError("illegal status transition was accepted")


def test_schema_non_negative_checks_reject_invalid_values(engine):
    init_database(engine)
    from uuid import uuid4
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    user_id, task_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(insert(UserORM).values(user_id=user_id, created_at=now))
        connection.execute(insert(AnalysisTaskORM).values(
            task_id=task_id, user_id=user_id, idempotency_key="k", request_hash="h",
            query="q", status=TaskStatus.PENDING, max_rounds=1, created_at=now,
            updated_at=now, metadata_json={}, model_call_count=0, model_duration_ms=0,
        ))
        checks = [
            ("datasets", {"dataset_id": uuid4(), "user_id": user_id, "name": "n", "source_uri": "u", "content_type": "t", "size_bytes": -1, "created_at": now, "metadata_json": {}}),
            ("analysis_tasks", {"task_id": uuid4(), "user_id": user_id, "idempotency_key": "k2", "request_hash": "h", "query": "q", "status": TaskStatus.PENDING, "max_rounds": 0, "created_at": now, "updated_at": now, "metadata_json": {}, "model_call_count": 0, "model_duration_ms": 0}),
            ("analysis_tasks", {"task_id": uuid4(), "user_id": user_id, "idempotency_key": "k3", "request_hash": "h", "query": "q", "status": TaskStatus.PENDING, "max_rounds": 1, "created_at": now, "updated_at": now, "metadata_json": {}, "model_call_count": -1, "model_duration_ms": 0}),
            ("analysis_tasks", {"task_id": uuid4(), "user_id": user_id, "idempotency_key": "k4", "request_hash": "h", "query": "q", "status": TaskStatus.PENDING, "max_rounds": 1, "created_at": now, "updated_at": now, "metadata_json": {}, "model_call_count": 0, "model_duration_ms": -1}),
        ]
        for table_name, values in checks:
            table = Base.metadata.tables[table_name]
            try:
                connection.execute(insert(table).values(values))
            except IntegrityError:
                continue
            raise AssertionError(f"negative invariant accepted for {table_name}")

        tool_call_id = uuid4()
        connection.execute(insert(Base.metadata.tables["tool_calls"]).values(
            tool_call_id=tool_call_id, task_id=task_id, tool_name="tool",
            arguments_json={}, result_json=None, status="PENDING",
        ))
        artifact_id = uuid4()
        connection.execute(insert(Base.metadata.tables["artifacts"]).values(
            artifact_id=artifact_id, task_id=task_id, artifact_type="CHART", name="x",
            size_bytes=0, metadata_json={}, created_at=now,
        ))
        for table_name, values in [
            ("executions", {"execution_result_id": uuid4(), "tool_call_id": tool_call_id, "success": True, "output_text": "", "variables_json": {}, "duration_ms": -1, "created_at": now}),
            ("artifacts", {"artifact_id": uuid4(), "task_id": task_id, "artifact_type": "CHART", "name": "x", "size_bytes": -1, "metadata_json": {}, "created_at": now}),
            ("reports", {"report_id": uuid4(), "artifact_id": artifact_id, "task_id": task_id, "format": "MARKDOWN", "storage_uri": "u", "size_bytes": -1, "created_at": now}),
        ]:
            try:
                connection.execute(insert(Base.metadata.tables[table_name]).values(values))
            except IntegrityError:
                continue
            raise AssertionError(f"negative invariant accepted for {table_name}")
