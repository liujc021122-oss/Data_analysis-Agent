from uuid import uuid4

import pytest
from pydantic import BaseModel

from data_analysis_agent import DataAnalysisAgent, ToolExecutor, ToolRegistry
from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools import ToolDefinition, ToolRiskLevel


class Input(BaseModel):
    value: int


class Output(BaseModel):
    result: int


def make_registry(handler):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="double_value",
            description="double",
            input_model=Input,
            output_model=Output,
            side_effect=False,
            network_access=False,
            max_runtime_seconds=1,
            required_permissions=frozenset(),
            risk_level=ToolRiskLevel.LOW,
        ),
        handler,
    )
    return registry


def test_agent_executes_typed_tool_with_agent_task_and_context():
    seen = []
    owner_id = uuid4()
    task_id = uuid4()
    registry = make_registry(
        lambda value, context: (
            seen.append((value.value, context)),
            {"result": value.value * 2},
        )[1]
    )
    agent = DataAnalysisAgent(
        llm=object(),
        dataset_owner_id=owner_id,
        task_id=task_id,
        tool_registry=registry,
        generate_word_report=False,
    )

    result = agent.execute_tool(
        "double_value",
        {"value": 4},
        permissions=("reports:read",),
        network_allowed=True,
    )

    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.task_id == task_id
    assert result.output == {"result": 8}
    assert seen[0][0] == 4
    assert seen[0][1].task_id == task_id
    assert seen[0][1].user_id == owner_id
    assert seen[0][1].permissions == frozenset({"reports:read"})
    assert seen[0][1].network_allowed is True
    assert agent.conversation_history[-1]["tool_name"] == "double_value"
    assert agent.conversation_history[-1]["output"] == {"result": 8}
    assert agent.conversation_history[-1]["success"] is True


def test_agent_uses_executor_for_registry_and_preserves_executor_only_compatibility():
    registry = make_registry(lambda value, context: {"result": value.value})
    executor = ToolExecutor(registry)

    from_registry = DataAnalysisAgent(
        llm=object(),
        tool_registry=registry,
        generate_word_report=False,
    )
    from_executor = DataAnalysisAgent(
        llm=object(),
        tool_executor=executor,
        generate_word_report=False,
    )

    assert isinstance(from_registry.tool_executor, ToolExecutor)
    assert from_registry.tool_executor.registry is registry
    assert from_executor.tool_executor is executor


def test_agent_rejects_mismatched_registry_and_executor():
    registry = make_registry(lambda value, context: {"result": value.value})
    other_registry = make_registry(lambda value, context: {"result": value.value})

    with pytest.raises(ValueError, match="same registry"):
        DataAnalysisAgent(
            llm=object(),
            tool_registry=registry,
            tool_executor=ToolExecutor(other_registry),
            generate_word_report=False,
        )


def test_default_agent_keeps_legacy_mode_and_execute_tool_fails_clearly():
    agent = DataAnalysisAgent(llm=object(), generate_word_report=False)

    assert agent.tool_executor is None
    with pytest.raises(RuntimeError, match="tool executor"):
        agent.execute_tool("missing", {})
