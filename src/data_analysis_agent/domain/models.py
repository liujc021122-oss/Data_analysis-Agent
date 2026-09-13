from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time, timezone
from enum import Enum
import json
import math
from types import MappingProxyType
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


class FrozenDict(Mapping[str, Any]):
    __slots__ = ("_data",)

    def __init__(self, value: Mapping[str, Any] | None = None, /, **items: Any) -> None:
        data = dict(value or ())
        data.update(items)
        object.__setattr__(self, "_data", MappingProxyType(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "FrozenDict":
        copied = type(self)(
            {deepcopy(key, memo): deepcopy(value, memo) for key, value in self.items()}
        )
        memo[id(self)] = copied
        return copied


def _freeze_nested(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return FrozenDict(
            {
                _freeze_nested(key): _freeze_nested(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze_nested(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_nested(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_nested(item) for item in value)
    return value


_JSON_CONTAINER_FIELDS = (
    "metadata",
    "arguments",
    "result",
    "variables",
    "context",
)
_DOMAIN_TIME_FIELDS = (
    "created_at",
    "updated_at",
    "occurred_at",
    "started_at",
    "finished_at",
)


def _canonical_json_value(value: Any) -> Any:
    """Return an immutable, JSON-native representation of a metadata value."""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _normalize_utc_datetime(value).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON metadata floats must be finite")
        return value
    if type(value) is str:
        return value
    if isinstance(value, Mapping):
        items = []
        for key, item in value.items():
            canonical_key = _canonical_json_value(key)
            if not isinstance(canonical_key, str):
                canonical_key = str(canonical_key)
            items.append((canonical_key, _canonical_json_value(item)))
        return FrozenDict(dict(items))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_json_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical_json_value(item) for item in value]
        return tuple(
            sorted(
                canonical_items,
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
        )
    raise ValueError(
        f"value of type {type(value).__name__} is not JSON-serializable metadata"
    )


def _normalize_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("domain datetimes must be timezone-aware")
    return value.astimezone(timezone.utc)


def _serialize_nested(value: Any, mode: str) -> Any:
    if isinstance(value, datetime):
        return value.isoformat() if mode == "json" else value
    if isinstance(value, FrozenDict):
        return {key: _serialize_nested(item, mode) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_serialize_nested(item, mode) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_serialize_nested(item, mode) for item in value)
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        validate_assignment=True,
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any):
        """Construct a domain snapshot while preserving ``_fields_set`` semantics.

        Pydantic's trusted construction path skips validation, which would also
        skip this model's recursive immutable normalization. Domain snapshots
        therefore use the strict validation path for every public constructor.
        """
        model = cls.model_validate(values)
        if _fields_set is not None:
            object.__setattr__(model, "__pydantic_fields_set__", _fields_set)
        return model

    @field_validator("*", mode="after")
    @classmethod
    def _freeze_nested_values(cls, value: Any) -> Any:
        return _freeze_nested(value)

    @field_validator(*_JSON_CONTAINER_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _canonicalize_json_fields(cls, value: Any) -> Any:
        return _canonical_json_value(value)

    @field_validator(*_DOMAIN_TIME_FIELDS, mode="after", check_fields=False)
    @classmethod
    def _normalize_domain_times(cls, value: datetime | None) -> datetime | None:
        return _normalize_utc_datetime(value) if value is not None else None

    @field_serializer("*", when_used="always")
    def _serialize_value(self, value: Any, info: Any) -> Any:
        return _serialize_nested(value, info.mode)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False):
        values = self.__dict__.copy()
        if deep:
            values = deepcopy(values)
        if update:
            values.update(update)
        return type(self).model_validate(values)


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
