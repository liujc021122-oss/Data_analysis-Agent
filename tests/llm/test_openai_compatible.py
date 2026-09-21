import asyncio
from types import SimpleNamespace

from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm import (
    ChatMessage,
    ChatRequest,
    OpenAICompatibleProvider,
)


class FakeCompletions:
    def __init__(self, response=None, chunks=()):
        self.response = response
        self.chunks = chunks
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            return self.response

        async def stream_chunks():
            for chunk in self.chunks:
                yield chunk

        return stream_chunks()


class FakeClient:
    def __init__(self, response=None, chunks=()):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response=response, chunks=chunks)
        )
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


def message(role, content):
    return ChatMessage(role=role, content=content)


def test_chat_uses_factory_kwargs_and_maps_request():
    asyncio.run(_test_chat_uses_factory_kwargs_and_maps_request())


async def _test_chat_uses_factory_kwargs_and_maps_request():
    config = LLMConfig(
        provider="deepseek",
        api_key="secret",
        base_url="https://example.invalid/v1",
        model="deepseek-chat",
        timeout_seconds=12.5,
    )
    client = FakeClient(
        response=SimpleNamespace(
            id="req-1",
            model="deepseek-chat",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello"),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    )
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return client

    provider = OpenAICompatibleProvider(config, client_factory=factory)
    request = ChatRequest(
        messages=(message("system", "be concise"), message("user", "hi")),
        model="custom-model",
        temperature=0.7,
        max_tokens=321,
    )

    response = await provider.chat(request)

    assert factory_calls == [
        {"api_key": "secret", "base_url": "https://example.invalid/v1", "timeout": 12.5}
    ]
    assert client.chat.completions.calls == [
        {
            "model": "custom-model",
            "messages": [
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "hi"},
            ],
            "temperature": 0.7,
            "max_tokens": 321,
        }
    ]
    assert response.text == "hello"


def test_reasoning_model_omits_temperature_and_uses_config_model():
    asyncio.run(_test_reasoning_model_omits_temperature_and_uses_config_model())


async def _test_reasoning_model_omits_temperature_and_uses_config_model():
    client = FakeClient(
        response=SimpleNamespace(
            id=None,
            model=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason=None)],
            usage=None,
        )
    )
    provider = OpenAICompatibleProvider(
        LLMConfig(api_key="secret", model="deepseek-r1"), client_factory=lambda **_: client
    )

    await provider.chat(ChatRequest(messages=(message("user", "solve"),), max_tokens=None))

    assert client.chat.completions.calls[0] == {
        "model": "deepseek-r1",
        "messages": [{"role": "user", "content": "solve"}],
    }


def test_chat_maps_response_usage_and_close_is_idempotent():
    asyncio.run(_test_chat_maps_response_usage_and_close_is_idempotent())


async def _test_chat_maps_response_usage_and_close_is_idempotent():
    client = FakeClient(
        response=SimpleNamespace(
            id="req-2",
            model="provider-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="length")],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
        )
    )
    provider = OpenAICompatibleProvider(
        LLMConfig(api_key="secret", model="configured-model"), client_factory=lambda **_: client
    )

    response = await provider.chat(ChatRequest(messages=(message("user", "question"),)))
    await provider.close()
    await provider.close()

    assert response.request_id == "req-2"
    assert response.model == "provider-model"
    assert response.finish_reason == "length"
    assert response.usage.input_tokens == 4
    assert response.usage.output_tokens == 6
    assert response.usage.total_tokens == 10
    assert client.close_calls == 1


def test_stream_maps_chunks_in_order_without_network_calls():
    asyncio.run(_test_stream_maps_chunks_in_order_without_network_calls())


async def _test_stream_maps_chunks_in_order_without_network_calls():
    chunks = [
        SimpleNamespace(
            id="stream-1",
            model="stream-model",
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"), finish_reason=None)],
            usage=None,
        ),
        SimpleNamespace(
            id="stream-1",
            model="stream-model",
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        ),
    ]
    client = FakeClient(chunks=chunks)
    provider = OpenAICompatibleProvider(
        LLMConfig(api_key="secret", model="stream-model"), client_factory=lambda **_: client
    )

    result = [chunk async for chunk in provider.stream(ChatRequest(messages=(message("user", "hi"),)))]

    assert [(chunk.text, chunk.finish_reason, chunk.request_id) for chunk in result] == [
        ("hel", None, "stream-1"),
        ("lo", "stop", "stream-1"),
    ]
    assert result[1].model == "stream-model"
    assert result[1].usage.total_tokens == 4
    assert client.chat.completions.calls[0]["stream"] is True
