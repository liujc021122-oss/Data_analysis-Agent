from uuid import uuid4

import pytest
from pydantic import BaseModel

from data_analysis_agent.tools.errors import UnknownToolError
from data_analysis_agent.tools.models import ToolDefinition, ToolRiskLevel
from data_analysis_agent.tools.registry import ToolRegistry


class EmptyInput(BaseModel):
    pass


class EmptyOutput(BaseModel):
    pass


def definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_model=EmptyInput,
        output_model=EmptyOutput,
        side_effect=False,
        network_access=False,
        max_runtime_seconds=1,
        required_permissions=frozenset(),
        risk_level=ToolRiskLevel.LOW,
    )


def test_registry_enforces_unique_names_and_sorted_discovery():
    registry = ToolRegistry()
    handler = lambda value, context: EmptyOutput()
    registry.register(definition("z_tool"), handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition("z_tool"), handler)
    registry.register(definition("a_tool"), handler)
    assert [item.name for item in registry.list_definitions()] == ["a_tool", "z_tool"]
    assert [item["function"]["name"] for item in registry.model_schemas()] == [
        "a_tool",
        "z_tool",
    ]


def test_registry_rejects_non_callable_handlers():
    with pytest.raises(TypeError, match="callable"):
        ToolRegistry().register(definition("tool"), object())


def test_registry_rejects_unknown_tool_and_preserves_task_id():
    task_id = uuid4()
    with pytest.raises(UnknownToolError) as raised:
        ToolRegistry().get("not_registered", task_id=task_id)
    assert raised.value.task_id == task_id


def test_unknown_tool_can_omit_task_id():
    error = None
    with pytest.raises(UnknownToolError) as raised:
        ToolRegistry().get("not_registered")
    error = raised.value
    assert error.task_id is None


def test_model_schemas_are_snapshots():
    registry = ToolRegistry()
    registry.register(definition("tool"), lambda value, context: EmptyOutput())

    schemas = registry.model_schemas()
    original_schema = registry.model_schemas()[0]
    schemas[0]["function"]["parameters"]["properties"] = {"changed": {}}

    fresh_schema = registry.model_schemas()[0]
    assert fresh_schema == original_schema


def test_discovery_results_are_tuples():
    registry = ToolRegistry()
    registry.register(definition("tool"), lambda value, context: EmptyOutput())

    assert isinstance(registry.list_definitions(), tuple)
    assert isinstance(registry.model_schemas(), tuple)
