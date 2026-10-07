import asyncio
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm import (
    ChatMessage,
    LLMClient,
    LLMAuthenticationError,
    LLMError,
    LLMStructuredOutputError,
    ProviderResponse,
    StructuredOutputRequest,
)
from data_analysis_agent.services.llm import LLMHelper


class Answer(BaseModel):
    result: str


@dataclass
class FakeProvider:
    responses: list[ProviderResponse]

    def __post_init__(self):
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self):
        return None


def run(coro):
    return asyncio.run(coro)


def make_request(**kwargs):
    return StructuredOutputRequest(
        messages=(
            ChatMessage(
                role="user",
                content="Return the answer without exposing api_key=secret-value.",
            ),
        ),
        response_model=Answer,
        **kwargs,
    )


def make_client(provider):
    return LLMClient(LLMConfig(api_key="secret-value"), provider=provider)


def test_valid_json_returns_typed_pydantic_value_and_original_metrics():
    provider = FakeProvider(
        [ProviderResponse(text='{"result":"ok"}', provider="fake", model="chat")]
    )
    response = run(make_client(provider).astructured_output(make_request()))

    assert isinstance(response.value, Answer)
    assert response.value.result == "ok"
    assert response.metrics.provider == "fake"
    assert response.metrics.attempt_count == 1
    assert len(provider.calls) == 1


def test_invalid_first_response_gets_one_schema_correction_request():
    provider = FakeProvider(
        [
            ProviderResponse(
                text='{"result": 42, "provider_payload":"do-not-repeat"}',
                provider="fake",
                model="chat",
            ),
            ProviderResponse(text='```json\n{"result":"fixed"}\n```', provider="fake", model="chat"),
        ]
    )
    response = run(make_client(provider).astructured_output(make_request()))

    assert response.value == Answer(result="fixed")
    assert len(provider.calls) == 2
    correction = provider.calls[1].messages[-1]
    assert correction.role == "user"
    assert "structured output validation failed" in correction.content
    assert "result" in correction.content
    assert "secret-value" not in correction.content
    assert "provider_payload" not in correction.content
    assert "Return the answer" not in correction.content


def test_fenced_yaml_response_with_preamble_returns_typed_value():
    provider = FakeProvider(
        [
            ProviderResponse(
                text="I will inspect the data first.\n\n```yaml\nresult: ok\n```",
                provider="fake",
                model="chat",
            ),
            ProviderResponse(
                text="```yaml\nresult: ok\n```",
                provider="fake",
                model="chat",
            ),
        ]
    )

    response = run(make_client(provider).astructured_output(make_request()))

    assert response.value == Answer(result="ok")
    assert len(provider.calls) == 1


def test_fenced_yaml_response_with_trailing_fields_returns_typed_value():
    provider = FakeProvider(
        [
            ProviderResponse(
                text=(
                    "I will inspect the data first.\n\n"
                    "```yaml\nresult: ok\n```\n"
                    "next_steps: []"
                ),
                provider="fake",
                model="chat",
            )
        ]
    )

    response = run(make_client(provider).astructured_output(make_request()))

    assert response.value == Answer(result="ok")
    assert len(provider.calls) == 1


def test_second_invalid_response_raises_without_returning_typed_value():
    provider = FakeProvider(
        [
            ProviderResponse(text="not json", provider="fake", model="chat"),
            ProviderResponse(text='{"wrong":"shape"}', provider="fake", model="chat"),
        ]
    )

    with pytest.raises(LLMStructuredOutputError, match="structured output validation failed") as exc_info:
        run(make_client(provider).astructured_output(make_request()))

    assert exc_info.value.attempts == 2
    assert len(provider.calls) == 2


def test_correction_gateway_error_preserves_original_error_class():
    provider = FakeProvider(
        [
            ProviderResponse(text="not json", provider="fake", model="chat"),
            LLMAuthenticationError("authentication failed"),
        ]
    )

    with pytest.raises(LLMAuthenticationError):
        run(make_client(provider).astructured_output(make_request()))


def test_raw_json_schema_validation_returns_json_value():
    provider = FakeProvider(
        [ProviderResponse(text='{"count":3}', provider="fake", model="chat")]
    )
    request = StructuredOutputRequest(
        messages=(ChatMessage(role="user", content="count"),),
        json_schema={
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        },
    )

    response = run(make_client(provider).astructured_output(request))

    assert response.value == {"count": 3}


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"response_model": Answer, "json_schema": {"type": "object"}},
    ],
)
def test_schema_source_validation_rejects_neither_or_both_before_provider_call(kwargs):
    provider = FakeProvider([])
    messages = (ChatMessage(role="user", content="count"),)

    with pytest.raises(ValueError, match="exactly one of response_model or json_schema"):
        StructuredOutputRequest(messages=messages, **kwargs)

    assert provider.calls == []


def test_helper_gateway_call_propagates_llm_error_and_yaml_parser_stays_compatible():
    class FailingGateway:
        def chat(self, request):
            raise LLMError("gateway failed")

    helper = LLMHelper.__new__(LLMHelper)
    helper.gateway = FailingGateway()
    helper.config = LLMConfig(api_key="secret-value")

    with pytest.raises(LLMError, match="gateway failed"):
        helper.call("private prompt")

    assert helper.parse_yaml_response("action: generate_code\ncode: pass") == {
        "action": "generate_code",
        "code": "pass",
    }
