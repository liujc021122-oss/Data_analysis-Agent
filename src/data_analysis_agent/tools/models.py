from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ToolContext(ToolModel):
    task_id: UUID
    user_id: UUID | None = None
    permissions: frozenset[str] = frozenset()
    network_allowed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | float | None = Field(default=None, ge=0)

    def to_agent_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["success"] = self.status is ToolCallStatus.SUCCEEDED
        return payload
