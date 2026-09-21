from datetime import datetime
from typing import Any, Literal, Mapping, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatMessage(GatewayModel):
    role: Literal["system", "user", "assistant"]
    content: StrictStr = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def content_must_be_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatRequest(GatewayModel):
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    model: Optional[str] = None
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class ProviderUsage(GatewayModel):
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    estimated: bool = False


class ProviderResponse(GatewayModel):
    text: Optional[str] = None
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    finish_reason: Optional[str] = None
    request_id: Optional[str] = None
    usage: Optional[ProviderUsage] = None


class ProviderChunk(GatewayModel):
    text: Optional[str] = None
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    finish_reason: Optional[str] = None
    request_id: Optional[str] = None
    usage: Optional[ProviderUsage] = None


def _timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class LLMCallMetrics(GatewayModel):
    call_id: UUID
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    attempt_count: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    usage: Optional[ProviderUsage] = None
    estimated_cost_usd: Optional[float] = Field(default=None, ge=0)
    request_id: Optional[str] = None

    _started_at_aware = field_validator("started_at")(_timezone_aware)
    _finished_at_aware = field_validator("finished_at")(_timezone_aware)


class LLMResponse(GatewayModel):
    text: StrictStr = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    finish_reason: Optional[str] = None
    request_id: Optional[str] = None
    metrics: LLMCallMetrics

    @field_validator("text")
    @classmethod
    def text_must_be_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class LLMStreamEvent(GatewayModel):
    kind: Literal["chunk", "completed"]
    text: Optional[str] = None
    metrics: Optional[LLMCallMetrics] = None

    @model_validator(mode="after")
    def completed_event_requires_metrics(self):
        if self.kind == "completed" and self.metrics is None:
            raise ValueError("completed stream events require metrics")
        return self


class StructuredOutputRequest(ChatRequest):
    response_model: Optional[type[BaseModel]] = None
    json_schema: Optional[Mapping[str, Any]] = None

    @field_serializer("response_model", when_used="json")
    def serialize_response_model(self, value: Optional[type[BaseModel]]) -> Optional[str]:
        return value.__qualname__ if value is not None else None

    @model_validator(mode="after")
    def require_one_output_contract(self):
        if (self.response_model is None) == (self.json_schema is None):
            raise ValueError("exactly one of response_model or json_schema is required")
        return self


class StructuredOutputResponse(GatewayModel):
    value: Any
    metrics: LLMCallMetrics
