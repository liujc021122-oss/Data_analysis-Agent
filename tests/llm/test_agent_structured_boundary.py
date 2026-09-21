import asyncio
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from data_analysis_agent import DataAnalysisAgent
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.llm import (
    ChatMessage,
    LLMClient,
    LLMStructuredOutputError,
    ProviderResponse,
)
from data_analysis_agent.services.llm import LLMHelper
from tests.fixtures.fake_llm import FakeLLM, yaml_response


@dataclass
class FakeProvider:
    responses: list

    def __post_init__(self):
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        return None


class NoCallExecutor:
    def __init__(self):
        self.calls = []

    def execute_code(self, code):
        self.calls.append(code)
        return {"success": True}


def run(coro):
    return asyncio.run(coro)


def test_agent_action_rejects_unknown_action_before_executor_access():
    from data_analysis_agent.agent.schemas import AgentAction

    with pytest.raises(ValidationError):
        AgentAction(action="future_action", code="x=1")


def test_gateway_structured_failure_propagates_without_executor_call():
    class FailingStructuredHelper:
        def structured_output(self, request):
            raise LLMStructuredOutputError("invalid structured action")

    agent = object.__new__(DataAnalysisAgent)
    agent.llm = FailingStructuredHelper()
    agent.config = LLMConfig(api_key="offline-key")
    agent.executor = NoCallExecutor()

    with pytest.raises(LLMStructuredOutputError):
        agent._request_structured_action("prompt", "system")

    assert agent.executor.calls == []


def test_real_gateway_unknown_action_never_reaches_executor():
    provider = FakeProvider(
        [
            ProviderResponse(
                text='{"action":"future_action","code":"x=1"}',
                provider="fake",
                model="chat",
            ),
            ProviderResponse(
                text='{"action":"future_action","code":"x=1"}',
                provider="fake",
                model="chat",
            ),
        ]
    )
    config = LLMConfig(api_key="offline-key")
    gateway = LLMClient(config, provider=provider)
    helper = LLMHelper(config, gateway=gateway)
    agent = object.__new__(DataAnalysisAgent)
    agent.llm = helper
    agent.config = config
    agent.executor = NoCallExecutor()

    with pytest.raises(LLMStructuredOutputError):
        agent._request_structured_action("prompt", "system")

    assert agent.executor.calls == []


def test_legacy_fake_llm_uses_explicit_yaml_compatibility_branch():
    from data_analysis_agent.agent.llm_port import AgentLLMPort

    helper = FakeLLM([yaml_response("generate_code", code="x = 1")])
    action = AgentLLMPort(helper, LLMConfig(api_key="offline-key")).request_action(
        "prompt", "system"
    )

    assert action.action == "generate_code"
    assert action.code == "x = 1"
    assert len(helper.calls) == 1
