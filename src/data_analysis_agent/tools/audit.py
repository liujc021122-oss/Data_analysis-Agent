from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
import json
import math
from typing import Any, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.domain.models import ExecutionResult, ToolCall
from data_analysis_agent.tools.errors import ToolAuditError
from data_analysis_agent.tools.models import ToolModel


_REDACTED = "[REDACTED]"
_UNSERIALIZABLE = "[UNSERIALIZABLE]"
_MAX_STRING_LENGTH = 256
_MAX_ITEMS = 20
_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "token", "password", "secret")
_PATH_KEY_NAMES = {"path", "uri", "url"}


class ToolAuditRecord(ToolModel):
    """An immutable, JSON-safe snapshot of one tool call."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_assignment=True, validate_default=True
    )

    call_id: UUID
    task_id: UUID
    tool_name: str
    status: ToolCallStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int | float = Field(ge=0)
    arguments: Mapping[str, Any] = Field(default_factory=dict)
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("arguments", mode="before")
    @classmethod
    def _snapshot_arguments(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("arguments must be a mapping")
        return _snapshot(value)

    @field_validator("arguments", mode="after")
    @classmethod
    def _freeze_arguments(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze(value)

    @field_validator("output", "error_code", "error_message", mode="before")
    @classmethod
    def _snapshot_value(cls, value: Any) -> Any:
        return _snapshot(value)

    @field_validator("duration_ms")
    @classmethod
    def _require_finite_duration(cls, value: int | float) -> int | float:
        if not math.isfinite(value):
            raise ValueError("duration_ms must be finite")
        return value

    @model_validator(mode="after")
    def _finished_after_started(self) -> "ToolAuditRecord":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        return self


class ToolCallRecorder(Protocol):
    def record(self, record: ToolAuditRecord) -> None:
        """Persist one audit record."""


class InMemoryToolCallRecorder:
    def __init__(self) -> None:
        self._records: list[ToolAuditRecord] = []

    @property
    def records(self) -> tuple[ToolAuditRecord, ...]:
        return tuple(self._records)

    def record(self, record: ToolAuditRecord) -> None:
        self._records.append(ToolAuditRecord.model_validate(record.model_dump()))


class RepositoryToolCallRecorder:
    """Persist tool-call audit snapshots through the existing repositories."""

    def __init__(self, uow_factory: Any) -> None:
        self._uow_factory = uow_factory

    def record(self, record: ToolAuditRecord) -> None:
        output_text = _output_text(record.output)
        try:
            with self._uow_factory() as uow:
                uow.tool_calls.add(
                    ToolCall(
                        tool_call_id=record.call_id,
                        task_id=record.task_id,
                        tool_name=record.tool_name,
                        arguments=dict(record.arguments),
                        result=_tool_call_result(record.output, output_text),
                        status=record.status,
                        started_at=record.started_at,
                        finished_at=record.finished_at,
                        error_message=record.error_message,
                    )
                )
                uow.executions.add(
                    ExecutionResult(
                        success=record.status is ToolCallStatus.SUCCEEDED,
                        output=output_text,
                        error=record.error_message,
                        variables=record.output if isinstance(record.output, Mapping) else {},
                        duration_ms=int(record.duration_ms),
                    ),
                    tool_call_id=record.call_id,
                )
        except Exception:
            raise ToolAuditError(record.tool_name, record.task_id) from None


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=True, separators=(",", ":"))


def _tool_call_result(output: Any, output_text: str) -> dict[str, Any] | str | None:
    if output is None or isinstance(output, str):
        return output
    if isinstance(output, Mapping):
        return dict(output)
    return output_text


def _snapshot(value: Any, *, _seen: set[int] | None = None) -> Any:
    """Convert arbitrary values to bounded, immutable JSON-safe data."""
    seen = set() if _seen is None else _seen

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _UNSERIALIZABLE
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, (UUID, datetime, Enum)):
        return _snapshot(str(value.value if isinstance(value, Enum) else value), _seen=seen)

    value_id = id(value)
    if value_id in seen:
        return "[CIRCULAR]"
    if isinstance(value, Mapping):
        seen.add(value_id)
        items: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            key_text = _snapshot(str(key), _seen=seen)
            if not isinstance(key_text, str):
                key_text = _UNSERIALIZABLE
            items[key_text] = (
                _REDACTED
                if _should_redact_key(key_text)
                else _snapshot(item, _seen=seen)
            )
        seen.remove(value_id)
        return _FrozenDict(items)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        seen.add(value_id)
        result = tuple(_snapshot(item, _seen=seen) for item in list(value)[:_MAX_ITEMS])
        seen.remove(value_id)
        return result
    if isinstance(value, (set, frozenset)):
        seen.add(value_id)
        result = tuple(_snapshot(item, _seen=seen) for item in list(value)[:_MAX_ITEMS])
        seen.remove(value_id)
        return result
    return _UNSERIALIZABLE


def _should_redact_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return (
        any(part in normalized for part in _SENSITIVE_KEY_PARTS)
        or normalized in _PATH_KEY_NAMES
        or normalized.endswith(("_path", "_uri", "_url"))
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


class _FrozenDict(dict[str, Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("audit snapshot is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable
