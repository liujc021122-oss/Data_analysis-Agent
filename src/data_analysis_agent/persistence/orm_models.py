from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..domain.enums import ReportFormat, TaskEventType, TaskStatus, ToolCallStatus
from .database import Base, UTCDateTime, UUIDString


def enum_column(enum_type, constraint_name: str):
    return Enum(
        enum_type,
        name=constraint_name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class UserORM(Base):
    __tablename__ = "users"

    user_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)


class DatasetORM(Base):
    __tablename__ = "datasets"
    __table_args__ = (Index("ix_datasets_user_id", "user_id"),)

    dataset_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    user_id: Mapped[Any] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text(), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    checksum: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)


class AnalysisTaskORM(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_analysis_tasks_user_idempotency"),
        Index("ix_analysis_tasks_status", "status"),
        Index("ix_analysis_tasks_created_at", "created_at"),
        Index("ix_analysis_tasks_status_created_at", "status", "created_at"),
    )

    task_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    user_id: Mapped[Any] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[Any] = mapped_column(enum_column(TaskStatus, "ck_analysis_tasks_status"), nullable=False)
    max_rounds: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text())
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    model_call_count: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    model_duration_ms: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)


task_dataset_link = Table(
    "analysis_task_datasets",
    Base.metadata,
    Column("task_id", UUIDString(), ForeignKey("analysis_tasks.task_id"), primary_key=True),
    Column("dataset_id", UUIDString(), ForeignKey("datasets.dataset_id"), primary_key=True),
    Index("ix_analysis_task_datasets_dataset_id", "dataset_id"),
)


class TaskEventORM(Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_occurred", "task_id", "occurred_at"),)

    event_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    task_id: Mapped[Any] = mapped_column(ForeignKey("analysis_tasks.task_id"), nullable=False)
    event_type: Mapped[Any] = mapped_column(enum_column(TaskEventType, "ck_task_events_event_type"), nullable=False)
    from_status: Mapped[Any | None] = mapped_column(enum_column(TaskStatus, "ck_task_events_from_status"))
    to_status: Mapped[Any] = mapped_column(enum_column(TaskStatus, "ck_task_events_to_status"), nullable=False)
    message: Mapped[str | None] = mapped_column(Text())
    occurred_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)


class ToolCallORM(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_task_id", "task_id"),)

    tool_call_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    task_id: Mapped[Any] = mapped_column(ForeignKey("analysis_tasks.task_id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    result_json: Mapped[Any] = mapped_column(JSON())
    status: Mapped[Any] = mapped_column(enum_column(ToolCallStatus, "ck_tool_calls_status"), nullable=False)
    started_at: Mapped[Any | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[Any | None] = mapped_column(UTCDateTime())
    error_message: Mapped[str | None] = mapped_column(Text())


class ExecutionORM(Base):
    __tablename__ = "executions"

    execution_result_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    tool_call_id: Mapped[Any | None] = mapped_column(ForeignKey("tool_calls.tool_call_id"))
    success: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    output_text: Mapped[str] = mapped_column(Text(), nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text())
    variables_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger())
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)


class ArtifactORM(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_task_id", "task_id"),)

    artifact_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    task_id: Mapped[Any] = mapped_column(ForeignKey("analysis_tasks.task_id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text())
    format: Mapped[Any | None] = mapped_column(enum_column(ReportFormat, "ck_artifacts_format"))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text())
    source_tool_call_id: Mapped[Any | None] = mapped_column(ForeignKey("tool_calls.tool_call_id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)


class ReportORM(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_task_id", "task_id"), UniqueConstraint("artifact_id", name="uq_reports_artifact_id"))

    report_id: Mapped[Any] = mapped_column(UUIDString(), primary_key=True)
    artifact_id: Mapped[Any] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    task_id: Mapped[Any] = mapped_column(ForeignKey("analysis_tasks.task_id"), nullable=False)
    format: Mapped[Any] = mapped_column(enum_column(ReportFormat, "ck_reports_format"), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text(), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
