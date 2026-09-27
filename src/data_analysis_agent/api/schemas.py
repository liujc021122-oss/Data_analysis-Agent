from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)

from ..domain.enums import ReportFormat, TaskEventType, TaskStatus
from ..datasets.models import DatasetProfile
from .pagination import PageResponse


_BLOCKED_PUBLIC_METADATA_KEYS = frozenset({"file_path", "source_uri", "storage_uri"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


def _sanitize_public_metadata(value: Any) -> Any:
    """Remove storage and local path fields from nested public metadata."""
    if isinstance(value, dict):
        return {
            key: _sanitize_public_metadata(item)
            for key, item in value.items()
            if key not in _BLOCKED_PUBLIC_METADATA_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_public_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_public_metadata(item) for item in value)
    return value


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    _sanitize_metadata = field_validator(
        "metadata", mode="before", check_fields=False
    )(_sanitize_public_metadata)


class AnalysisTaskCreateRequest(APIModel):
    query: StrictStr
    idempotency_key: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    max_rounds: StrictInt = Field(default=10, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)
    _validate_idempotency_key = field_validator("idempotency_key")(_nonblank)


class ErrorResponse(APIModel):
    code: StrictStr
    message: StrictStr
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: StrictStr


class ArtifactResponse(APIModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    artifact_type: StrictStr
    name: StrictStr
    download_url: StrictStr | None = None
    format: ReportFormat | None = None
    mime_type: StrictStr | None = None
    description: StrictStr | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisTaskResponse(APIModel):
    task_id: UUID
    query: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    status: TaskStatus
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime
    updated_at: datetime
    error: ErrorResponse | None = None
    artifacts: tuple[ArtifactResponse, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)


class TaskSubmissionResponse(APIModel):
    task_id: UUID
    status: TaskStatus
    created: StrictBool
    enqueued: StrictBool


AnalysisTaskSubmissionResponse = TaskSubmissionResponse


class TaskEventResponse(APIModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: TaskEventType
    from_status: TaskStatus | None = None
    to_status: TaskStatus
    message: StrictStr | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResultResponse(APIModel):
    success: StrictBool
    output: StrictStr = ""
    error: StrictStr | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)


class DatasetUploadResponse(APIModel):
    dataset_id: UUID
    profile: DatasetProfile


class DatasetResponse(APIModel):
    dataset_id: UUID
    name: StrictStr
    content_type: StrictStr
    size_bytes: StrictInt = Field(ge=0)
    checksum: StrictStr | None = None
    created_at: datetime
    profile: DatasetProfile


class DatasetListResponse(PageResponse[DatasetResponse]):
    pass


class TaskListResponse(PageResponse[AnalysisTaskResponse]):
    pass


class TaskEventListResponse(PageResponse[TaskEventResponse]):
    pass


class ArtifactDownloadResponse(APIModel):
    artifact_id: UUID
    download_url: StrictStr
    expires_in: StrictInt = Field(gt=0)
