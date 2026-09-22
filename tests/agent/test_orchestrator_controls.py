import data_analysis_agent.agent.orchestrator as orchestrator_module
from data_analysis_agent.agent.orchestration_models import (
    OrchestratorLimits,
    StageFailure,
    StageResult,
)
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import AnalysisTask


def _empty_handlers():
    return {
        stage: lambda stage_input, call_tool: StageResult()
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }


def test_retryable_stage_failure_retries_then_completes():
    calls = []
    task = AnalysisTask(query="retry")

    def flaky(stage_input, call_tool):
        calls.append(stage_input.attempt)
        if len(calls) == 1:
            return StageResult(
                completed=False,
                failure=StageFailure(
                    code="TEMPORARY_MODEL_ERROR",
                    message="retry offline",
                    retryable=True,
                ),
            )
        return StageResult(output={"ok": True})

    handlers = _empty_handlers()
    handlers[TaskStatus.EXPLORING] = flaky
    result = AgentOrchestrator(
        task=task,
        handlers=handlers,
        limits=OrchestratorLimits(max_stage_retries=1),
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert calls == [0, 1]
    assert any(event.event_type is TaskEventType.ERROR for event in result.state.events)


def test_non_retryable_failure_enters_failed_without_advancing():
    calls = []
    handlers = _empty_handlers()

    def fail(stage_input, call_tool):
        calls.append(stage_input.stage)
        return StageResult(
            completed=False,
            failure=StageFailure(code="MODEL_SCHEMA_ERROR", message="bad shape"),
        )

    handlers[TaskStatus.ANALYZING] = fail
    result = AgentOrchestrator(task=AnalysisTask(query="fail"), handlers=handlers).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_STAGE_FAILED"
    assert calls == [TaskStatus.ANALYZING]
    error_event = next(event for event in result.state.events if event.event_type is TaskEventType.ERROR)
    assert dict(error_event.metadata) == {
        "stage": "ANALYZING",
        "step_number": 3,
        "attempt": 0,
        "cause_code": "MODEL_SCHEMA_ERROR",
    }
    assert dict(result.state.events[-1].metadata) == {
        "code": "ORCHESTRATOR_STAGE_FAILED"
    }


def test_max_steps_stops_a_non_completing_stage():
    calls = []
    handlers = _empty_handlers()

    def keep_running(stage_input, call_tool):
        calls.append(stage_input.step_number)
        return StageResult(completed=False)

    handlers[TaskStatus.ANALYZING] = keep_running
    result = AgentOrchestrator(
        task=AnalysisTask(query="budget"),
        handlers=handlers,
        limits=OrchestratorLimits(max_steps=5),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_MAX_STEPS"
    assert len(calls) == 2


def test_incomplete_stage_does_not_consume_a_retry_attempt():
    attempts = []
    handlers = _empty_handlers()

    def incomplete_once(stage_input, call_tool):
        attempts.append(stage_input.attempt)
        if len(attempts) == 1:
            return StageResult(completed=False)
        return StageResult()

    handlers[TaskStatus.ANALYZING] = incomplete_once
    result = AgentOrchestrator(
        task=AnalysisTask(query="incomplete"), handlers=handlers
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert attempts == [0, 0]


def test_cancel_after_a_stage_prevents_the_next_stage():
    calls = []
    orchestrator = None

    def cancel_in_running(stage_input, call_tool):
        calls.append(stage_input.stage)
        orchestrator.cancel()
        return StageResult()

    handlers = {
        stage: lambda stage_input, call_tool: (calls.append(stage), StageResult())[1]
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }
    handlers[TaskStatus.RUNNING] = cancel_in_running
    orchestrator = AgentOrchestrator(task=AnalysisTask(query="cancel"), handlers=handlers)

    result = orchestrator.run()

    assert result.status is TaskStatus.CANCELLED
    assert calls == [TaskStatus.RUNNING]
    assert result.error_code == "ORCHESTRATOR_CANCELLED"


def test_model_call_budget_is_checked_before_starting_a_new_stage():
    calls = []
    handlers = {
        stage: lambda stage_input, call_tool: (
            calls.append(stage),
            StageResult(model_calls=1),
        )[1]
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }

    result = AgentOrchestrator(
        task=AnalysisTask(query="model budget"),
        handlers=handlers,
        limits=OrchestratorLimits(max_model_calls=1),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_MAX_MODEL_CALLS"
    assert len(calls) == 1


def test_runtime_budget_stops_before_the_next_stage(monkeypatch):
    readings = iter([10.0, 10.0, 12.0])
    monkeypatch.setattr(
        orchestrator_module,
        "monotonic",
        lambda: next(readings),
    )
    handlers = _empty_handlers()

    result = AgentOrchestrator(
        task=AnalysisTask(query="timeout"),
        handlers=handlers,
        limits=OrchestratorLimits(max_runtime_seconds=1.0),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_TIMEOUT"


def test_handler_exception_produces_safe_terminal_failure_event():
    handlers = _empty_handlers()

    def explode(stage_input, call_tool):
        raise RuntimeError("secret=do-not-expose")

    handlers[TaskStatus.RUNNING] = explode
    result = AgentOrchestrator(task=AnalysisTask(query="exception"), handlers=handlers).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_STAGE_FAILED"
    error_event = next(event for event in result.state.events if event.event_type is TaskEventType.ERROR)
    assert dict(error_event.metadata) == {
        "stage": "RUNNING",
        "step_number": 0,
        "attempt": 0,
        "cause_code": "STAGE_EXECUTION_ERROR",
    }
    assert "secret" not in result.error_message


def test_rejected_model_call_count_does_not_spend_budget():
    handlers = _empty_handlers()
    handlers[TaskStatus.RUNNING] = lambda stage_input, call_tool: StageResult(model_calls=2)

    result = AgentOrchestrator(
        task=AnalysisTask(query="overspend"),
        handlers=handlers,
        limits=OrchestratorLimits(max_model_calls=1),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_MAX_MODEL_CALLS"
    assert result.checkpoint.task.model_call_count == 0
    error_event = next(event for event in result.state.events if event.event_type is TaskEventType.ERROR)
    assert dict(error_event.metadata) == {
        "stage": "RUNNING",
        "step_number": 0,
        "attempt": 0,
        "cause_code": "ORCHESTRATOR_MAX_MODEL_CALLS",
    }


def test_checkpoint_records_elapsed_handler_runtime(monkeypatch):
    readings = iter([10.0, 12.5])
    monkeypatch.setattr(orchestrator_module, "monotonic", lambda: next(readings))
    orchestrator = None

    def cancel_after_running(stage_input, call_tool):
        orchestrator.cancel()
        return StageResult()

    handlers = _empty_handlers()
    handlers[TaskStatus.RUNNING] = cancel_after_running
    orchestrator = AgentOrchestrator(task=AnalysisTask(query="elapsed"), handlers=handlers)

    result = orchestrator.run()

    assert result.status is TaskStatus.CANCELLED
    assert result.checkpoint.elapsed_runtime_seconds == 2.5
