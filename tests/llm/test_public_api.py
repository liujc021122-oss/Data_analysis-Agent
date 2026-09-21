import asyncio

import data_analysis_agent

from data_analysis_agent import (
    ChatRequest,
    LLMClient,
    LLMConfig,
    LLMConfigurationError,
    OpenAICompatibleProvider,
)
from data_analysis_agent.llm import (
    ChatRequest as CanonicalChatRequest,
    LLMClient as CanonicalLLMClient,
    LLMConfig as CanonicalLLMConfig,
    LLMConfigurationError as CanonicalConfigurationError,
    OpenAICompatibleProvider as CanonicalProvider,
)
from data_analysis_agent.services.llm import LLMHelper


class AsyncCloseSpy:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        self.calls += 1


class InjectedLLM:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def run(coro):
    return asyncio.run(coro)


def test_root_exports_are_canonical_llm_objects_without_provider_construction():
    assert data_analysis_agent.LLMClient is CanonicalLLMClient
    assert LLMClient is CanonicalLLMClient
    assert LLMConfig is CanonicalLLMConfig
    assert LLMConfigurationError is CanonicalConfigurationError
    assert OpenAICompatibleProvider is CanonicalProvider
    assert ChatRequest is CanonicalChatRequest

    # Constructing the gateway itself is offline and lazy; no provider or API
    # call is required merely to import the package or create the client.
    client = LLMClient(LLMConfig(api_key=None))
    assert client.provider is None


def test_helper_gateway_close_is_idempotent():
    gateway = AsyncCloseSpy()
    helper = LLMHelper(LLMConfig(api_key="offline-key"), gateway=gateway)

    run(helper.close())
    run(helper.close())

    assert gateway.calls == 1


def test_agent_does_not_close_injected_llm(tmp_path):
    injected = InjectedLLM()
    agent = data_analysis_agent.DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline-key"),
        output_dir=str(tmp_path),
        llm=injected,
    )

    agent._close_owned_llm()

    assert injected.close_calls == 0


def test_agent_closes_owned_helper_after_analysis(tmp_path):
    agent = data_analysis_agent.DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline-key"),
        output_dir=str(tmp_path),
    )
    agent._analyze_impl = lambda *args, **kwargs: {"ok": True}

    assert agent.analyze("offline") == {"ok": True}
    assert agent.llm._closed is True
