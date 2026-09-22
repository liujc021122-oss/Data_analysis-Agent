from datetime import datetime, timezone
import math
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


def definition(name="double_value", runtime=1.5, required_permissions=frozenset()):
    return ToolDefinition(
        name=name, description="Double an integer.", input_model=Input,
        output_model=Output, side_effect=False, network_access=False,
        max_runtime_seconds=runtime, required_permissions=required_permissions,
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


def test_definition_copies_required_permissions_into_an_immutable_set():
    permissions = {"reports:read"}

    item = definition(required_permissions=permissions)
    permissions.add("reports:write")

    assert item.required_permissions == frozenset({"reports:read"})
    assert isinstance(item.required_permissions, frozenset)
    with pytest.raises(AttributeError):
        item.required_permissions.add("execute:python")


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


def test_result_requires_completed_timing_fields():
    with pytest.raises(ValidationError):
        ToolCallResult(
            call_id=uuid4(), task_id=uuid4(), tool_name="double_value",
            status=ToolCallStatus.FAILED, output=None,
        )


@pytest.mark.parametrize(
    "value",
    [object(), datetime.now(timezone.utc), uuid4(), math.nan, math.inf, (1, 2), {1: "not-json"}],
)
def test_result_rejects_non_json_output_values(value):
    with pytest.raises(ValidationError):
        ToolCallResult(
            call_id=uuid4(), task_id=uuid4(), tool_name="double_value",
            status=ToolCallStatus.SUCCEEDED, output=value,
            started_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc),
            finished_at=datetime(2026, 9, 21, 12, 0, 0, 1_000, tzinfo=timezone.utc),
            duration_ms=1,
        )


def test_result_requires_aware_ordered_timestamps_and_nonnegative_duration():
    common = {
        "call_id": uuid4(), "task_id": uuid4(), "tool_name": "double_value",
        "status": ToolCallStatus.SUCCEEDED, "output": {"doubled": 4},
    }
    with pytest.raises(ValidationError):
        ToolCallResult(**common, started_at=datetime(2026, 9, 21, 12),
                       finished_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc), duration_ms=0)
    with pytest.raises(ValidationError):
        ToolCallResult(**common, started_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc),
                       finished_at=datetime(2026, 9, 21, 11, 59, tzinfo=timezone.utc), duration_ms=0)
    with pytest.raises(ValidationError):
        ToolCallResult(**common, started_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc),
                       finished_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc), duration_ms=-1)
    with pytest.raises(ValidationError):
        ToolCallResult(**common, started_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc),
                       finished_at=datetime(2026, 9, 21, 12, tzinfo=timezone.utc), duration_ms=math.inf)


def test_context_metadata_is_deeply_immutable_and_detached_from_input():
    metadata = {"audit": {"tags": ["private"], "attributes": {"path": "secret"}}}
    context = ToolContext(task_id=uuid4(), metadata=metadata)

    metadata["audit"]["tags"].append("changed")
    assert context.metadata["audit"]["tags"] == ("private",)
    with pytest.raises(TypeError):
        context.metadata["audit"]["attributes"]["path"] = "changed"
    with pytest.raises(AttributeError):
        context.metadata["audit"]["tags"].append("changed")
