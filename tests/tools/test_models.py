from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.models import (
    ToolCallRequest, ToolCallResult, ToolContext, ToolDefinition, ToolRiskLevel,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


def definition(name="double_value", runtime=1.5):
    return ToolDefinition(
        name=name, description="Double an integer.", input_model=Input,
        output_model=Output, side_effect=False, network_access=False,
        max_runtime_seconds=runtime, required_permissions=frozenset(),
        risk_level=ToolRiskLevel.LOW,
    )


def test_definition_exports_schema_and_rejects_bad_metadata():
    schema = definition().to_model_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "double_value"
    assert schema["function"]["parameters"]["properties"]["value"]["type"] == "integer"
    with pytest.raises(ValueError, match="name"):
        definition("run python")
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        definition(runtime=0)


def test_request_result_preserve_ids_and_are_json_serializable():
    task_id = uuid4()
    request = ToolCallRequest(task_id=task_id, tool_name="double_value", arguments={"value": 2})
    result = ToolCallResult(
        call_id=request.call_id, task_id=task_id, tool_name=request.tool_name,
        status=ToolCallStatus.SUCCEEDED, output={"doubled": 4},
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00", duration_ms=10,
    )
    payload = result.to_agent_payload()
    assert payload["task_id"] == str(task_id)
    assert payload["call_id"] == str(request.call_id)
    assert payload["success"] is True
    assert result.model_dump_json()


def test_context_requires_task_id_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ToolContext.model_validate({"permissions": [], "network_allowed": False})
    with pytest.raises(ValidationError):
        ToolContext(task_id=uuid4(), unexpected=True)
