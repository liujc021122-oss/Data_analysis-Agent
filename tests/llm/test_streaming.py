import asyncio
from dataclasses import dataclass

import pytest

from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm import (
    ChatMessage,
    ChatRequest,
    LLMClient,
    LLMTimeoutError,
    ProviderChunk,
    ProviderUsage,
)


@dataclass
class FakeStreamProvider:
    streams: list

    def __post_init__(self):
        self.calls = []

    async def chat(self, request):  # pragma: no cover - stream-only double
        raise AssertionError("chat must not be used for streaming")

    async def stream(self, request):
        self.calls.append(request)
        stream = self.streams.pop(0)
        for item in stream:
            if isinstance(item, BaseException):
                raise item
            yield item

    async def close(self):
        return None


class Recorder:
    def __init__(self):
        self.items = []

    def record(self, metrics):
        self.items.append(metrics)


def request():
    return ChatRequest(
        messages=(ChatMessage(role="user", content="stream the result"),),
    )


def run(coro):
    return asyncio.run(coro)


def chunk(text=None, *, usage=None):
    return ProviderChunk(
        text=text,
        provider="fake",
        model="chat",
        usage=usage,
    )


def test_astream_emits_ordered_chunks_and_one_completion_event():
    recorder = Recorder()
    provider = FakeStreamProvider([[chunk("a"), chunk("b")]])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider, recorder=recorder)

    events = run(collect(client.astream(request())))

    assert [(event.kind, event.text) for event in events] == [
        ("chunk", "a"),
        ("chunk", "b"),
        ("completed", None),
    ]
    assert events[-1].metrics.attempt_count == 1
    assert events[-1].metrics.duration_ms >= 0
    assert len(recorder.items) == 1


def test_astream_skips_empty_and_whitespace_only_chunks():
    provider = FakeStreamProvider([[chunk(""), chunk("  "), chunk("a")]])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider)

    events = run(collect(client.astream(request())))

    assert [(event.kind, event.text) for event in events] == [
        ("chunk", "a"),
        ("completed", None),
    ]


def test_astream_does_not_replay_a_chunk_after_stream_failure():
    recorder = Recorder()
    provider = FakeStreamProvider([[chunk("a"), TimeoutError()]])
    client = LLMClient(
        LLMConfig(api_key="key", max_attempts=3),
        provider=provider,
        recorder=recorder,
    )

    async def consume():
        seen = []
        with pytest.raises(LLMTimeoutError):
            async for event in client.astream(request()):
                seen.append(event)
        return seen

    events = run(consume())

    assert [(event.kind, event.text) for event in events] == [("chunk", "a")]
    assert len(provider.calls) == 1
    assert len(recorder.items) == 1
    assert recorder.items[0].attempt_count == 1


def test_astream_retries_before_first_chunk_only():
    recorder = Recorder()
    provider = FakeStreamProvider(
        [
            [TimeoutError()],
            [chunk("a")],
        ]
    )
    client = LLMClient(
        LLMConfig(api_key="key", max_attempts=2, backoff_base_seconds=0.1),
        provider=provider,
        recorder=recorder,
        sleep=lambda _delay: None,
        jitter=lambda: 0,
    )

    events = run(collect(client.astream(request())))

    assert [(event.kind, event.text) for event in events] == [
        ("chunk", "a"),
        ("completed", None),
    ]
    assert len(provider.calls) == 2
    assert events[-1].metrics.attempt_count == 2
    assert len(recorder.items) == 1


def test_astream_uses_final_provider_usage_chunk():
    usage = ProviderUsage(input_tokens=4, output_tokens=3, total_tokens=7)
    recorder = Recorder()
    provider = FakeStreamProvider([[chunk("a"), chunk(usage=usage)]])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider, recorder=recorder)

    events = run(collect(client.astream(request())))

    assert events[-1].metrics.usage == usage
    assert events[-1].metrics.usage.estimated is False
    assert recorder.items[0].usage == usage


def test_sync_stream_preserves_event_order():
    provider = FakeStreamProvider([[chunk("a"), chunk("b")]])
    client = LLMClient(LLMConfig(api_key="key"), provider=provider)

    events = list(client.stream(request()))

    assert [event.text for event in events[:-1]] == ["a", "b"]
    assert events[-1].kind == "completed"


def test_sync_stream_yields_chunks_before_a_later_failure():
    provider = FakeStreamProvider([[chunk("a"), TimeoutError()]])
    client = LLMClient(LLMConfig(api_key="key", max_attempts=1), provider=provider)

    events = client.stream(request())

    assert next(events).text == "a"
    with pytest.raises(LLMTimeoutError):
        next(events)


async def collect(events):
    return [event async for event in events]
