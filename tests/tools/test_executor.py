import asyncio
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.audit import InMemoryToolCallRecorder
from data_analysis_agent.tools.errors import ToolDependencyError
from data_analysis_agent.tools.executor import ToolExecutor
from data_analysis_agent.tools.models import (
    ToolCallRequest,
    ToolContext,
    ToolDefinition,
    ToolRiskLevel,
)
from data_analysis_agent.tools.registry import ToolRegistry


class AddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: int
    right: int


class AddOutput(BaseModel):
    total: int


def make_executor(
    handler,
    *,
    runtime=1,
    permissions=frozenset(),
    network=False,
    risk=ToolRiskLevel.LOW,
    recorder=None,
):
    definition = ToolDefinition(
        name="add",
        description="add",
        input_model=AddInput,
        output_model=AddOutput,
        side_effect=False,
        network_access=network,
        max_runtime_seconds=runtime,
        required_permissions=permissions,
        risk_level=risk,
    )
    registry = ToolRegistry()
    registry.register(definition, handler)
    return ToolExecutor(registry, recorder=recorder)


def context(task_id, *, permissions=frozenset(), network_allowed=False):
    return ToolContext(
        task_id=task_id,
        permissions=permissions,
        network_allowed=network_allowed,
    )


def request(task_id, **arguments):
    return ToolCallRequest(task_id=task_id, tool_name="add", arguments=arguments)


def test_valid_input_reaches_handler_and_output_is_validated():
    seen = []
    recorder = InMemoryToolCallRecorder()
    task_id = uuid4()

    result = make_executor(
        lambda value, _: (seen.append(value), {"total": value.left + value.right})[1],
        recorder=recorder,
    ).execute(request(task_id, left=2, right=3), context(task_id))

    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"total": 5}
    assert seen[0].left == 2
    assert recorder.records[0].task_id == task_id
    assert result.started_at.tzinfo is not None
    assert result.finished_at.tzinfo is not None
    assert result.duration_ms >= 0


def test_invalid_input_never_enters_handler_and_handler_errors_are_sanitized():
    seen = []
    task_id = uuid4()
    executor = make_executor(lambda value, _: seen.append(value))

    invalid = executor.execute(
        request(task_id, left="bad", right=3),
        context(task_id),
    )
    assert invalid.error_code == "TOOL_INPUT_INVALID"
    assert seen == []

    def raises(value, context):
        raise RuntimeError("secret stack")

    failed = make_executor(raises).execute(
        request(task_id, left=1, right=2),
        context(task_id),
    )
    assert failed.error_code == "TOOL_EXECUTION_FAILED"
    assert "secret stack" not in (failed.error_message or "")
    assert "RuntimeError" not in (failed.error_message or "")


def test_known_tool_errors_preserve_their_stable_error_code():
    task_id = uuid4()

    def raises_dependency(value, context):
        raise ToolDependencyError("add", context.task_id)

    result = make_executor(raises_dependency).execute(
        request(task_id, left=1, right=2),
        context(task_id),
    )

    assert result.error_code == "TOOL_DEPENDENCY_FAILED"
    assert result.error_message == "Tool dependency failed"


def test_permissions_network_high_risk_and_timeout_are_checked():
    task_id = uuid4()
    no_permission = make_executor(
        lambda value, _: {"total": 1},
        permissions=frozenset({"reports:write"}),
        risk=ToolRiskLevel.HIGH,
    ).execute(request(task_id, left=1, right=2), context(task_id))
    assert no_permission.error_code == "TOOL_PERMISSION_DENIED"

    network_denied = make_executor(
        lambda value, _: {"total": 1},
        network=True,
    ).execute(request(task_id, left=1, right=2), context(task_id))
    assert network_denied.error_code == "TOOL_NETWORK_DENIED"

    high_risk_without_python = make_executor(
        lambda value, _: {"total": 1},
        risk=ToolRiskLevel.HIGH,
    ).execute(
        request(task_id, left=1, right=2),
        context(task_id, permissions=frozenset()),
    )
    assert high_risk_without_python.error_code == "TOOL_PERMISSION_DENIED"

    def slow(value, context):
        time.sleep(0.2)
        return {"total": 1}

    started = time.monotonic()
    timed_out = make_executor(slow, runtime=0.01).execute(
        request(task_id, left=1, right=2),
        context(task_id),
    )
    elapsed = time.monotonic() - started
    assert timed_out.status is ToolCallStatus.FAILED
    assert timed_out.error_code == "TOOL_TIMEOUT"
    assert elapsed < 0.15


def test_required_python_permission_allows_high_risk_handler():
    task_id = uuid4()
    result = make_executor(
        lambda value, _: {"total": value.left + value.right},
        risk=ToolRiskLevel.HIGH,
    ).execute(
        request(task_id, left=1, right=2),
        context(task_id, permissions=frozenset({"execute:python"})),
    )

    assert result.status is ToolCallStatus.SUCCEEDED


def test_unknown_tool_result_keeps_request_ids():
    task_id = uuid4()
    request_value = ToolCallRequest(task_id=task_id, tool_name="missing")

    result = ToolExecutor(ToolRegistry()).execute(
        request_value,
        context(task_id),
    )

    assert result.status is ToolCallStatus.FAILED
    assert result.error_code == "UNKNOWN_TOOL"
    assert result.call_id == request_value.call_id
    assert result.task_id == task_id


def test_context_mismatch_stops_before_handler_and_preserves_request_ids():
    seen = []
    request_task_id = uuid4()
    context_task_id = uuid4()
    executor = make_executor(lambda value, _: seen.append(value))
    request_value = request(request_task_id, left=1, right=2)

    result = executor.execute(request_value, context(context_task_id))

    assert result.error_code == "TOOL_CONTEXT_INVALID"
    assert result.call_id == request_value.call_id
    assert result.task_id == request_task_id
    assert seen == []


def test_bad_output_is_reported_without_leaking_validation_details():
    task_id = uuid4()
    result = make_executor(
        lambda value, _: {"wrong": "private field"},
    ).execute(request(task_id, left=1, right=2), context(task_id))

    assert result.error_code == "TOOL_OUTPUT_INVALID"
    assert "private field" not in (result.error_message or "")


def test_async_handler_is_awaited_and_audit_recorder_failures_do_not_replace_result():
    task_id = uuid4()
    seen = []

    async def handler(value, tool_context):
        await asyncio.sleep(0)
        seen.append(tool_context.task_id)
        return {"total": value.left + value.right}

    class BrokenRecorder:
        def record(self, record):
            raise RuntimeError("audit backend secret")

    result = make_executor(handler, recorder=BrokenRecorder()).execute(
        request(task_id, left=4, right=5),
        context(task_id),
    )

    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"total": 9}
    assert seen == [task_id]


def test_aexecute_returns_validated_result_for_async_callers():
    task_id = uuid4()

    async def handler(value, tool_context):
        await asyncio.sleep(0)
        return {"total": value.left + value.right}

    result = asyncio.run(
        make_executor(handler).aexecute(
            request(task_id, left=6, right=7),
            context(task_id),
        )
    )

    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"total": 13}


def test_aexecute_sync_handler_timeout_does_not_wait_for_worker_thread():
    task_id = uuid4()

    def slow(value, context):
        time.sleep(0.2)
        return {"total": 1}

    async def run():
        return await make_executor(slow, runtime=0.01).aexecute(
            request(task_id, left=1, right=2),
            context(task_id),
        )

    started = time.monotonic()
    result = asyncio.run(run())
    elapsed = time.monotonic() - started

    assert result.error_code == "TOOL_TIMEOUT"
    assert elapsed < 0.15
