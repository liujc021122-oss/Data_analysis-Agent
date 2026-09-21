from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm.models import (
    ChatMessage,
    ChatRequest,
    LLMCallMetrics,
    LLMResponse,
    ProviderUsage,
    StructuredOutputRequest,
)


def test_request_metrics_and_response_are_json_serializable():
    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Summarize these results."),),
        model="offline-model",
        max_tokens=128,
        metadata={"task": "summary"},
    )
    metrics = LLMCallMetrics(
        call_id=uuid4(),
        provider="offline",
        model="offline-model",
        attempt_count=1,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        duration_ms=0,
        usage=ProviderUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    response = LLMResponse(
        text="Summary complete.",
        provider="offline",
        model="offline-model",
        metrics=metrics,
    )

    assert request.model_dump_json()
    assert metrics.model_dump_json()
    assert response.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": ()},
        {
            "messages": (ChatMessage(role="user", content="Hello"),),
            "max_tokens": 0,
        },
    ],
)
def test_chat_request_rejects_empty_messages_and_zero_max_tokens(payload):
    with pytest.raises(ValidationError):
        ChatRequest(**payload)


def test_structured_output_request_requires_exactly_one_output_contract():
    class Answer(BaseModel):
        result: str

    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    structured_request = StructuredOutputRequest(
        **request.model_dump(), response_model=Answer
    )
    assert structured_request.response_model is Answer
    assert structured_request.model_dump_json()

    with pytest.raises(ValidationError):
        StructuredOutputRequest(**request.model_dump())

    with pytest.raises(ValidationError):
        StructuredOutputRequest(
            **request.model_dump(),
            response_model=Answer,
            json_schema={"type": "object"},
        )


def test_llm_config_defaults_validate_new_fields_and_hide_api_key():
    config = LLMConfig(api_key="private-key")

    assert config.timeout_seconds == 60.0
    assert config.max_attempts == 3
    assert config.backoff_base_seconds == 0.25
    assert config.backoff_max_seconds == 8.0
    assert config.validate() is True
    assert "private-key" not in repr(config)
    assert "private-key" not in config.to_dict().values()


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_seconds", 0),
        ("max_attempts", 0),
        ("backoff_base_seconds", 0),
        ("backoff_max_seconds", 0),
    ],
)
def test_llm_config_rejects_nonpositive_gateway_settings(field, value):
    with pytest.raises(ValueError, match=field):
        LLMConfig(api_key="offline-key", **{field: value}).validate()


def test_llm_config_model_prices_cannot_be_mutated():
    config = LLMConfig(api_key="offline-key", model_prices={"offline-model": 0.01})

    with pytest.raises(TypeError):
        config.model_prices["another-model"] = 0.02
