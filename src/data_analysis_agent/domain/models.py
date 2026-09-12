from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    field_serializer,
)

from .enums import ReportFormat, TaskEventType, TaskStatus, ToolCallStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    @field_serializer("*", when_used="json")
    def _serialize_json_value(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class Dataset(DomainModel):
    dataset_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    source_uri: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(default=0, ge=0)
    checksum: StrictStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_text = field_validator("name", "source_uri", "content_type")(_nonblank)


class AnalysisTask(DomainModel):
    task_id: UUID = Field(default_factory=uuid4)
    query: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)


class TaskEvent(DomainModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: TaskEventType
    from_status: TaskStatus | None = None
    to_status: TaskStatus
    message: StrictStr | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(DomainModel):
    tool_call_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    tool_name: StrictStr
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | StrictStr | None = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: StrictStr | None = None

    _validate_tool_name = field_validator("tool_name")(_nonblank)


class ExecutionResult(DomainModel):
    success: StrictBool
    output: StrictStr = ""
    error: StrictStr | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)


class MetricArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    value: StrictFloat
    unit: StrictStr | None = None
    description: StrictStr | None = None
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_name = field_validator("name")(_nonblank)


class ChartArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    filename: StrictStr
    file_path: StrictStr
    mime_type: StrictStr = "image/png"
    title: StrictStr | None = None
    description: StrictStr | None = None
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_filename = field_validator("filename")(_nonblank)
    _validate_file_path = field_validator("file_path")(_nonblank)


class ReportArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    format: ReportFormat
    file_path: StrictStr
    title: StrictStr | None = None
    content_hash: StrictStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_file_path = field_validator("file_path")(_nonblank)


class AgentState(DomainModel):
    task_id: UUID
    status: TaskStatus = TaskStatus.PENDING
    current_round: StrictInt = Field(default=0, ge=0)
    events: tuple[TaskEvent, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    execution_results: tuple[ExecutionResult, ...] = ()
    metric_artifacts: tuple[MetricArtifact, ...] = ()
    chart_artifacts: tuple[ChartArtifact, ...] = ()
    report_artifacts: tuple[ReportArtifact, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)
