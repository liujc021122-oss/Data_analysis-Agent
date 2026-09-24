from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time, timezone
from enum import Enum
import json
import math
import re
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

from .enums import (
    EvidenceClaimKind,
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
    ReportFormat,
    TaskEventType,
    TaskStatus,
    ToolCallStatus,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


def _validate_evidence_sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("code_hash must be a 64-character hexadecimal digest")
    return value.lower()


def _finite_evidence_float(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("evidence numbers must be finite")
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
    "computed_at",
    "checked_at",
)


def _canonical_json_value(
    value: Any, _active_container_ids: set[int] | None = None
) -> Any:
    """Return an immutable, JSON-native representation of a metadata value."""
    active_container_ids = (
        _active_container_ids if _active_container_ids is not None else set()
    )
    if isinstance(value, Enum):
        return _canonical_json_value(value.value, active_container_ids)
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
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValueError("JSON metadata contains a circular reference")
        active_container_ids.add(container_id)
        try:
            items = []
            canonical_keys = set()
            for key, item in value.items():
                canonical_key = _canonical_json_value(key, active_container_ids)
                if not isinstance(canonical_key, str):
                    raise ValueError("JSON metadata mapping keys must be strings")
                if canonical_key in canonical_keys:
                    raise ValueError(
                        "JSON metadata mapping keys must be unique after canonicalization"
                    )
                canonical_keys.add(canonical_key)
                items.append(
                    (
                        canonical_key,
                        _canonical_json_value(item, active_container_ids),
                    )
                )
            return FrozenDict(items)
        finally:
            active_container_ids.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValueError("JSON metadata contains a circular reference")
        active_container_ids.add(container_id)
        try:
            return tuple(
                _canonical_json_value(item, active_container_ids) for item in value
            )
        finally:
            active_container_ids.remove(container_id)
    if isinstance(value, (set, frozenset)):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValueError("JSON metadata contains a circular reference")
        active_container_ids.add(container_id)
        try:
            canonical_items = [
                _canonical_json_value(item, active_container_ids) for item in value
            ]
            try:
                sorted_items = sorted(
                    canonical_items,
                    key=lambda item: json.dumps(
                        _json_sort_shape(item),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "JSON metadata set items must have a stable JSON representation"
                ) from exc
            return tuple(sorted_items)
        finally:
            active_container_ids.remove(container_id)
    raise ValueError(
        f"value of type {type(value).__name__} is not JSON-serializable metadata"
    )


def _json_sort_shape(value: Any) -> Any:
    """Return a mutable JSON-ready shape for canonical collection sorting."""
    if isinstance(value, FrozenDict):
        return {key: _json_sort_shape(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_sort_shape(item) for item in value]
    return value


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
    model_call_count: StrictInt = Field(default=0, ge=0)
    model_duration_ms: StrictInt = Field(default=0, ge=0)

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
    task_id: UUID | None = None
    name: StrictStr
    value: StrictFloat
    unit: StrictStr | None = None
    description: StrictStr | None = None
    formula: StrictStr | None = None
    source_columns: tuple[StrictStr, ...] = ()
    source_dataset_ids: tuple[UUID, ...] = ()
    execution_id: UUID | None = None
    code_hash: StrictStr | None = None
    computed_at: datetime | None = None
    verification_status: EvidenceVerificationStatus = (
        EvidenceVerificationStatus.UNVERIFIED
    )
    recomputed_value: StrictFloat | None = None
    tolerance: StrictFloat = Field(default=1e-6, ge=0)
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_name = field_validator("name")(_nonblank)
    _validate_code_hash = field_validator("code_hash")(
        lambda value: None if value is None else _validate_evidence_sha256(value)
    )
    _validate_values = field_validator("value", "recomputed_value")(
        lambda value: None if value is None else _finite_evidence_float(value)
    )
    _validate_tolerance = field_validator("tolerance")(_finite_evidence_float)


class ChartArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    task_id: UUID | None = None
    filename: StrictStr
    file_path: StrictStr
    mime_type: StrictStr = "image/png"
    title: StrictStr | None = None
    description: StrictStr | None = None
    chart_type: StrictStr | None = None
    source_metric_ids: tuple[UUID, ...] = ()
    execution_id: UUID | None = None
    code_hash: StrictStr | None = None
    verification_status: EvidenceVerificationStatus = (
        EvidenceVerificationStatus.UNVERIFIED
    )
    checked_at: datetime | None = None
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    size_bytes: StrictInt = Field(default=0, ge=0)
    content_hash: StrictStr | None = None

    _validate_filename = field_validator("filename")(_nonblank)
    _validate_file_path = field_validator("file_path")(_nonblank)
    _validate_code_hash = field_validator("code_hash")(
        lambda value: None if value is None else _validate_evidence_sha256(value)
    )


class EvidenceClaim(DomainModel):
    claim_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    text: StrictStr
    kind: EvidenceClaimKind
    metric_ids: tuple[UUID, ...] = ()
    chart_ids: tuple[UUID, ...] = ()
    status: EvidenceClaimStatus = EvidenceClaimStatus.PENDING_CONFIRMATION
    created_at: datetime = Field(default_factory=utc_now)

    _validate_text = field_validator("text")(_nonblank)


class EvidenceValidation(DomainModel):
    task_id: UUID
    valid: StrictBool
    claims: tuple[EvidenceClaim, ...] = ()
    unsupported_numeric_claims: tuple[StrictStr, ...] = ()
    missing_chart_ids: tuple[UUID, ...] = ()
    error_codes: tuple[StrictStr, ...] = ()
    checked_at: datetime = Field(default_factory=utc_now)


class ReportArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    format: ReportFormat
    file_path: StrictStr
    title: StrictStr | None = None
    content_hash: StrictStr | None = None
    size_bytes: StrictInt = Field(default=0, ge=0)
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
    evidence_claims: tuple[EvidenceClaim, ...] = ()
    evidence_validation: EvidenceValidation | None = None
    report_artifacts: tuple[ReportArtifact, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)
