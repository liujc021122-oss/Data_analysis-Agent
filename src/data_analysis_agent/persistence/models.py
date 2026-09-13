from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


class PersistenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _normalize_timestamps(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("persistence datetimes must be timezone-aware")
            return value.astimezone(timezone.utc)
        return value


class DatasetRecord(PersistenceModel):
    dataset_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    source_uri: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(default=0, ge=0)
    checksum: StrictStr | None = None
    created_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    user_id: UUID | None = None


class AnalysisTaskRecord(PersistenceModel):
    task_id: UUID = Field(default_factory=uuid4)
    query: StrictStr
    dataset_ids_json: list[StrictStr] = Field(default_factory=list)
    status: StrictStr = "PENDING"
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    user_id: UUID | None = None
    idempotency_key: StrictStr | None = None
    request_hash: StrictStr | None = None
    model_call_count: StrictInt = Field(default=0, ge=0)
    model_duration_ms: StrictInt = Field(default=0, ge=0)


class TaskEventRecord(PersistenceModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: StrictStr
    from_status: StrictStr | None = None
    to_status: StrictStr
    message: StrictStr | None = None
    occurred_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(PersistenceModel):
    tool_call_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    tool_name: StrictStr
    arguments_json: dict[str, Any] = Field(default_factory=dict)
    result_json: dict[str, Any] | StrictStr | None = None
    status: StrictStr = "PENDING"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: StrictStr | None = None


class ExecutionResultRecord(PersistenceModel):
    execution_result_id: UUID = Field(default_factory=uuid4)
    tool_call_id: UUID | None = None
    success: StrictBool
    output_text: StrictStr = ""
    error_text: StrictStr | None = None
    variables_json: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)


class ArtifactRecord(PersistenceModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    artifact_type: StrictStr
    name: StrictStr
    file_path: StrictStr | None = None
    format: StrictStr | None = None
    mime_type: StrictStr | None = None
    content_hash: StrictStr | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    size_bytes: StrictInt = Field(default=0, ge=0)
    description: StrictStr | None = None
    title: StrictStr | None = None
    source_tool_call_id: UUID | None = None


class UserRecord(PersistenceModel):
    user_id: UUID = Field(default_factory=uuid4)
    created_at: datetime


class ReportRecord(PersistenceModel):
    report_id: UUID = Field(default_factory=uuid4)
    artifact_id: UUID
    task_id: UUID
    format: StrictStr
    storage_uri: StrictStr
    size_bytes: StrictInt = Field(default=0, ge=0)
    content_hash: StrictStr | None = None
    created_at: datetime
