import asyncio
from dataclasses import dataclass
from collections.abc import Mapping

import httpx
import pytest

from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm.client import LLMClient
from data_analysis_agent.llm.errors import (
    LLMAuthenticationError,
    LLMClosedError,
    LLMConfigurationError,
    LLMEmptyResponseError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
)
from data_analysis_agent.llm.models import (
    ChatMessage,
    ChatRequest,
    ProviderResponse,
    ProviderUsage,
)
from openai import AuthenticationError, BadRequestError, RateLimitError


@dataclass
class FakeProvider:
    results: list
    calls: list = None
    closed: int = 0

    def __post_init__(self):
        self.calls = [] if self.calls is None else self.calls

    async def chat(self, request):
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self):
        self.closed += 1


class Recorder:
    def __init__(self):
        self.items = []

    def record(self, metrics):
        self.items.append(metrics)


def request(model=None):
    return ChatRequest(
        messages=(ChatMessage(role="user", content="summarize this"),), model=model
    )


def run(coro):
    return asyncio.run(coro)


def test_retries_timeouts_with_capped_backoff_and_records_attempts():
    provider = FakeProvider(
        [
            TimeoutError(),
            TimeoutError(),
            ProviderResponse(text="ok", provider="fake", model="chat"),
        ]
    )
    delays = []
    client = LLMClient(
        LLMConfig(api_key="key", backoff_base_seconds=0.1),
        provider=provider,
        sleep=lambda delay: delays.append(delay),
        jitter=lambda: 0,
    )

    response = run(client.achat(request()))

    assert response.text == "ok"
    assert len(provider.calls) == 3
    assert delays == [0.1, 0.2]
    assert response.metrics.attempt_count == 3
    assert response.metrics.duration_ms >= 0


def test_retries_empty_response_before_returning_success():
    provider = FakeProvider(
        [
            ProviderResponse(text="", provider="fake", model="chat"),
            ProviderResponse(text="ok", provider="fake", model="chat"),
        ]
    )
    delays = []
    client = LLMClient(
        LLMConfig(api_key="key", max_attempts=2, backoff_base_seconds=0.1),
        provider=provider,
        sleep=lambda delay: delays.append(delay),
        jitter=lambda: 0,
    )

    response = run(client.achat(request()))

    assert response.text == "ok"
    assert len(provider.calls) == 2
    assert delays == [0.1]
    assert response.metrics.attempt_count == 2


def test_authentication_and_request_errors_are_not_retried():
    for error_type, expected in [
        (LLMAuthenticationError, LLMAuthenticationError),
        (LLMRequestError, LLMRequestError),
    ]:
        provider = FakeProvider([error_type("bad")])
        client = LLMClient(LLMConfig(api_key="key"), provider=provider)
        with pytest.raises(expected):
            run(client.achat(request()))
        assert len(provider.calls) == 1


def test_missing_key_does_not_call_provider_or_recorder():
    provider = FakeProvider([])
    recorder = Recorder()
    client = LLMClient(LLMConfig(api_key=None), provider=provider, recorder=recorder)

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        run(client.achat(request()))

    assert provider.calls == []
    assert recorder.items == []


def test_missing_key_without_injected_provider_is_configuration_error():
    client = LLMClient(LLMConfig(api_key=None))

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        run(client.achat(request()))


def test_rate_limit_retries_and_override_model_reaches_provider():
    provider = FakeProvider(
        [
            LLMRateLimitError("slow"),
            ProviderResponse(text="ok", provider="fake", model="override"),
        ]
    )
    client = LLMClient(
        LLMConfig(api_key="key", model="default"),
        provider=provider,
        sleep=lambda _: None,
        jitter=lambda: 0,
    )

    run(client.achat(request("override")))
    assert len(provider.calls) == 2
    assert all(call.model == "override" for call in provider.calls)


def test_recorder_gets_success_and_final_failure_without_prompt_or_secret():
    recorder = Recorder()
    secret = "super-secret"
    provider = FakeProvider([LLMRequestError(f"failed {secret}")])
    client = LLMClient(
        LLMConfig(api_key=secret), provider=provider, recorder=recorder
    )
    with pytest.raises(LLMRequestError):
        run(client.achat(
            ChatRequest(
                messages=(ChatMessage(role="user", content="private prompt"),)
            )
        ))
    assert len(recorder.items) == 1
    assert secret not in repr(recorder.items[0])
    assert "private prompt" not in repr(recorder.items[0])


def test_close_is_idempotent_and_closed_client_rejects_calls():
    provider = FakeProvider([])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider)
    run(client.aclose())
    run(client.aclose())
    assert provider.closed == 1
    with pytest.raises(LLMClosedError):
        run(client.achat(request()))


def test_usage_and_estimated_cost_are_recorded():
    recorder = Recorder()
    provider = FakeProvider(
        [
            ProviderResponse(
                text="ok",
                provider="fake",
                model="chat",
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        ]
    )
    client = LLMClient(
        LLMConfig(
            api_key="key",
            model_prices=MappingProxyPrices({"chat": {"input": 2, "output": 4}}),
        ),
        provider=provider,
        recorder=recorder,
    )
    response = run(client.achat(request()))
    assert response.metrics.usage.estimated is False
    assert response.metrics.estimated_cost_usd == pytest.approx(0.00004)


class MappingProxyPrices(Mapping):
    def __init__(self, values):
        self.values = values

    def __getitem__(self, key):
        return self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


@pytest.mark.parametrize(
    "error_type,expected_type,status",
    [
        (AuthenticationError, LLMAuthenticationError, 401),
        (RateLimitError, LLMRateLimitError, 429),
        (BadRequestError, LLMRequestError, 400),
    ],
)
def test_openai_error_mapping_preserves_metadata_and_redacts_message(
    error_type, expected_type, status
):
    secret = "super-secret"
    prompt = "private prompt"
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://example.invalid"),
    )
    provider_error = error_type(f"raw {secret} {prompt}", response=response, body={"error": "raw"})
    client = LLMClient(
        LLMConfig(api_key=secret, max_attempts=1), provider=FakeProvider([provider_error])
    )

    with pytest.raises(expected_type) as raised:
        run(client.achat(ChatRequest(messages=(ChatMessage(role="user", content=prompt),))))

    assert secret not in str(raised.value)
    assert prompt not in str(raised.value)
    assert raised.value.status_code == status
    assert raised.value.provider == "deepseek"


def test_provider_supplied_llm_error_is_redacted_and_metadata_preserved():
    secret = "super-secret"
    prompt = "private prompt"
    provider_error = LLMRequestError(
        f"raw {secret} {prompt}", provider="fake-provider", status_code=422, retryable=False
    )
    client = LLMClient(LLMConfig(api_key=secret), provider=FakeProvider([provider_error]))

    with pytest.raises(LLMRequestError) as raised:
        run(client.achat(ChatRequest(messages=(ChatMessage(role="user", content=prompt),))))

    assert secret not in str(raised.value)
    assert prompt not in str(raised.value)
    assert raised.value.provider == "fake-provider"
    assert raised.value.status_code == 422


def test_real_wait_for_timeout_records_final_failure_metrics():
    async def never_returns(_request):
        await asyncio.sleep(0.05)

    provider = FakeProvider([])
    provider.chat = never_returns
    recorder = Recorder()
    client = LLMClient(
        LLMConfig(api_key="key", timeout_seconds=0.001, max_attempts=1),
        provider=provider,
        recorder=recorder,
    )

    with pytest.raises(LLMTimeoutError):
        run(client.achat(request()))

    assert len(recorder.items) == 1
    assert recorder.items[0].attempt_count == 1
    assert recorder.items[0].duration_ms > 0


def test_empty_response_records_metrics_before_raising():
    recorder = Recorder()
    provider = FakeProvider([ProviderResponse(text="", provider="fake", model="chat")])
    client = LLMClient(
        LLMConfig(api_key="key", max_attempts=1),
        provider=provider,
        recorder=recorder,
    )

    with pytest.raises(LLMEmptyResponseError):
        run(client.achat(request()))

    assert len(recorder.items) == 1
    assert recorder.items[0].attempt_count == 1


def test_missing_usage_is_estimated_from_request_and_response_text():
    provider = FakeProvider([ProviderResponse(text="two words", provider="fake", model="chat")])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider)

    response = run(client.achat(request()))

    assert response.metrics.usage.estimated is True
    assert response.metrics.usage.input_tokens == 2
    assert response.metrics.usage.output_tokens == 2


def test_sync_chat_and_close_use_event_loop():
    provider = FakeProvider([ProviderResponse(text="ok", provider="fake", model="chat")])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider)
    assert client.chat(request()).text == "ok"
    client.close()
