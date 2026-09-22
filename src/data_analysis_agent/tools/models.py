from dataclasses import dataclass
import copy
from datetime import datetime
from enum import Enum
import math
import re
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from data_analysis_agent.domain.enums import ToolCallStatus


class ToolRiskLevel(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    @field_validator("*", mode="after")
    @classmethod
    def _require_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class _FrozenDict(dict[str, Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("metadata is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_metadata(item) for key, item in copy.deepcopy(value).items()})
    if isinstance(value, list):
        return tuple(_freeze_metadata(item) for item in copy.deepcopy(value))
    if isinstance(value, tuple):
        return tuple(_freeze_metadata(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_metadata(item) for item in copy.deepcopy(value))
    return copy.deepcopy(value)


def _validate_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise ValueError("output must contain only finite JSON numbers")
    if isinstance(value, list):
        return [_validate_json_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("output object keys must be strings")
        return {key: _validate_json_value(item) for key, item in value.items()}
    raise ValueError("output must be JSON-safe")


class ToolContext(ToolModel):
    task_id: UUID
    user_id: UUID | None = None
    permissions: frozenset[str] = frozenset()
    network_allowed: bool = False
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_metadata(value)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effect: bool
    network_access: bool
    max_runtime_seconds: float
    required_permissions: frozenset[str]
    risk_level: ToolRiskLevel

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_permissions", frozenset(self.required_permissions))
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("name must match ^[a-z][a-z0-9_]{0,63}$")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must not be blank")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a BaseModel subclass")
        if not isinstance(self.output_model, type) or not issubclass(self.output_model, BaseModel):
            raise TypeError("output_model must be a BaseModel subclass")
        if not math.isfinite(self.max_runtime_seconds) or self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive and finite")

    def to_model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


class ToolCallRequest(ToolModel):
    call_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    task_id: UUID
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(ToolModel):
    call_id: UUID
    task_id: UUID
    tool_name: str
    status: ToolCallStatus
    output: JsonValue | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int | float = Field(ge=0)

    @field_validator("output", mode="before")
    @classmethod
    def _require_json_output(cls, value: Any) -> Any:
        return _validate_json_value(value)

    @field_validator("duration_ms")
    @classmethod
    def _require_finite_duration(cls, value: int | float) -> int | float:
        if not math.isfinite(value):
            raise ValueError("duration_ms must be finite")
        return value

    @model_validator(mode="after")
    def _finished_after_started(self) -> "ToolCallResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        return self

    def to_agent_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["success"] = self.status is ToolCallStatus.SUCCEEDED
        return payload
