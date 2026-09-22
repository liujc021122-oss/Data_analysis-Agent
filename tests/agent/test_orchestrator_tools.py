from pydantic import BaseModel

from data_analysis_agent.agent.orchestration_models import StageFailure, StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskEventType, TaskStatus, ToolCallStatus
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.tools import (
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRiskLevel,
)


class Input(BaseModel):
    value: int


class Output(BaseModel):
    result: int


def _registry(seen):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="validate_metric",
            description="test metric",
            input_model=Input,
            output_model=Output,
            side_effect=False,
            network_access=False,
            max_runtime_seconds=1,
            required_permissions=frozenset(),
            risk_level=ToolRiskLevel.LOW,
        ),
        lambda value, context: (
            seen.append((value.value, context.task_id)),
            {"result": value.value},
        )[1],
    )
    return registry


def _handlers():
    return {
        stage: lambda stage_input, call_tool: StageResult()
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }


def test_allowed_tool_is_executed_with_the_orchestrator_task_id_and_recorded():
    seen = []
    task = AnalysisTask(query="tool")
    handlers = _handlers()

    def validate(stage_input, call_tool):
        result = call_tool("validate_metric", {"value": 7})
        assert result.status is ToolCallStatus.SUCCEEDED
        return StageResult(output={"tool_result": result.output})

    handlers[TaskStatus.ANALYZING] = validate
    result = AgentOrchestrator(
        task=task, handlers=handlers, tool_executor=ToolExecutor(_registry(seen))
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert seen == [(7, task.task_id)]
    tool_event = next(
        event
        for event in result.state.events
        if event.event_type is TaskEventType.TOOL_CALLED
    )
    assert set(tool_event.metadata) == {
        "tool_name",
        "call_id",
        "status",
        "error_code",
        "duration_ms",
    }
    assert tool_event.metadata["tool_name"] == "validate_metric"
    assert result.state.tool_calls[0].tool_name == "validate_metric"
    assert result.state.tool_calls[0].arguments == {"value": 7}
    assert result.state.tool_calls[0].result == {"result": 7}


def test_tool_not_allowed_for_the_current_stage_never_reaches_the_handler():
    seen = []
    task = AnalysisTask(query="policy")
    handlers = _handlers()

    def explore(stage_input, call_tool):
        result = call_tool("validate_metric", {"value": 7})
        assert result.status is ToolCallStatus.FAILED
        assert result.error_code == "ORCHESTRATOR_TOOL_NOT_ALLOWED"
        return StageResult(
            completed=False,
            failure=StageFailure(
                code=result.error_code,
                message=result.error_message or "denied",
            ),
        )

    handlers[TaskStatus.EXPLORING] = explore
    result = AgentOrchestrator(
        task=task, handlers=handlers, tool_executor=ToolExecutor(_registry(seen))
    ).run()

    assert result.status is TaskStatus.FAILED
    assert seen == []
    assert (
        result.state.events[-2].metadata["cause_code"]
        == "ORCHESTRATOR_TOOL_NOT_ALLOWED"
    )


def test_tool_request_without_executor_returns_a_stable_failure():
    task = AnalysisTask(query="no executor")
    handlers = _handlers()

    def analyze(stage_input, call_tool):
        result = call_tool("run_sql", {"query": "select 1", "parameters": {}})
        assert result.status is ToolCallStatus.FAILED
        assert result.error_code == "ORCHESTRATOR_TOOL_UNAVAILABLE"
        return StageResult(
            completed=False,
            failure=StageFailure(
                code=result.error_code,
                message=result.error_message or "unavailable",
            ),
        )

    handlers[TaskStatus.ANALYZING] = analyze
    result = AgentOrchestrator(task=task, handlers=handlers).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_STAGE_FAILED"
    assert (
        result.state.events[-2].metadata["cause_code"]
        == "ORCHESTRATOR_TOOL_UNAVAILABLE"
    )


def test_cancelled_orchestrator_does_not_dispatch_a_tool():
    seen = []
    task = AnalysisTask(query="cancel tool")
    handlers = _handlers()
    orchestrator = None

    def analyze(stage_input, call_tool):
        orchestrator.cancel()
        result = call_tool("validate_metric", {"value": 7})
        assert result.status is ToolCallStatus.FAILED
        assert result.error_code == "ORCHESTRATOR_CANCELLED"
        return StageResult(completed=False)

    handlers[TaskStatus.ANALYZING] = analyze
    orchestrator = AgentOrchestrator(
        task=task, handlers=handlers, tool_executor=ToolExecutor(_registry(seen))
    )
    result = orchestrator.run()

    assert result.status is TaskStatus.CANCELLED
    assert seen == []
