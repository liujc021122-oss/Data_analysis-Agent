from datetime import datetime, timezone

import pytest

import data_analysis_agent.agent.orchestrator as orchestrator_module
from data_analysis_agent.agent.orchestration_models import OrchestratorLimits, StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask


def _handlers(trace, stage_inputs=None, context_updates=None):
    context_updates = context_updates or {}

    def make(stage):
        def handler(stage_input, call_tool):
            trace.append((stage, stage_input.stage, stage_input.attempt))
            if stage_inputs is not None:
                stage_inputs.append(stage_input)
            return StageResult(
                output={"stage": stage.value},
                context_updates=context_updates.get(stage, {}),
            )

        return handler

    return {stage: make(stage) for stage in AgentOrchestrator.ACTIVE_STAGES}


def test_orchestrator_runs_the_declared_stage_order_and_reaches_completed():
    trace = []
    task = AnalysisTask(query="offline flow")
    state = AgentState(task_id=task.task_id, context={"source": "offline"})
    original_task = task.model_copy(deep=True)
    original_state = state.model_copy(deep=True)
    stage_inputs = []
    orchestrator = AgentOrchestrator(
        task=task,
        handlers=_handlers(
            trace,
            stage_inputs,
            {TaskStatus.RUNNING: {"flow": "started"}},
        ),
        initial_state=state,
        limits=OrchestratorLimits(max_model_calls=7),
    )

    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert [stage for stage, _, _ in trace] == [
        TaskStatus.RUNNING,
        TaskStatus.EXPLORING,
        TaskStatus.CLEANING,
        TaskStatus.ANALYZING,
        TaskStatus.VALIDATING,
        TaskStatus.REPORTING,
    ]
    assert result.state.status is TaskStatus.COMPLETED
    assert [event.to_status for event in result.state.events] == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.EXPLORING,
        TaskStatus.CLEANING,
        TaskStatus.ANALYZING,
        TaskStatus.VALIDATING,
        TaskStatus.REPORTING,
        TaskStatus.COMPLETED,
    ]
    assert task == original_task
    assert state == original_state
    assert result.task_id == result.checkpoint.task.task_id
    assert [stage_input.stage for stage_input in stage_inputs] == list(
        AgentOrchestrator.ACTIVE_STAGES
    )
    assert [stage_input.allowed_tools for stage_input in stage_inputs] == [
        AgentOrchestrator.STAGE_ALLOWED_TOOLS[stage]
        for stage in AgentOrchestrator.ACTIVE_STAGES
    ]
    assert [stage_input.step_number for stage_input in stage_inputs] == list(range(6))
    assert [stage_input.attempt for stage_input in stage_inputs] == [0] * 6
    assert [stage_input.remaining_model_calls for stage_input in stage_inputs] == [7] * 6
    assert stage_inputs[0].context == {"source": "offline"}
    assert stage_inputs[1].context == {"source": "offline", "flow": "started"}


def test_incomplete_stage_repeats_before_advancing_to_its_successor(monkeypatch):
    trace = []
    stage_inputs = []
    analyzing_calls = 0
    handlers = _handlers(trace, stage_inputs)

    def analyzing_handler(stage_input, call_tool):
        nonlocal analyzing_calls
        analyzing_calls += 1
        trace.append((TaskStatus.ANALYZING, stage_input.stage, stage_input.attempt))
        stage_inputs.append(stage_input)
        if analyzing_calls == 1:
            return StageResult(completed=False, context_updates={"analysis": "pending"})
        return StageResult(output={"stage": TaskStatus.ANALYZING.value})

    handlers[TaskStatus.ANALYZING] = analyzing_handler
    task = AnalysisTask(query="offline flow")
    initial_state = AgentState(task_id=task.task_id)
    context_update_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(orchestrator_module, "utc_now", lambda: context_update_time)
    orchestrator = AgentOrchestrator(
        task=task,
        handlers=handlers,
        initial_state=initial_state,
    )

    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert [stage for stage, _, _ in trace] == [
        TaskStatus.RUNNING,
        TaskStatus.EXPLORING,
        TaskStatus.CLEANING,
        TaskStatus.ANALYZING,
        TaskStatus.ANALYZING,
        TaskStatus.VALIDATING,
        TaskStatus.REPORTING,
    ]
    analyzing_inputs = [
        stage_input
        for stage_input in stage_inputs
        if stage_input.stage is TaskStatus.ANALYZING
    ]
    assert [stage_input.attempt for stage_input in analyzing_inputs] == [0, 0]
    assert analyzing_inputs[1].context["analysis"] == "pending"
    assert analyzing_inputs[1].state.updated_at == context_update_time


def test_missing_active_stage_handler_is_rejected_before_execution():
    task = AnalysisTask(query="offline flow")
    handlers = _handlers([])
    handlers.pop(TaskStatus.REPORTING)

    with pytest.raises(ValueError, match="REPORTING"):
        AgentOrchestrator(task=task, handlers=handlers)


def test_task_and_state_status_mismatch_in_initial_state_is_rejected():
    task = AnalysisTask(query="offline flow")
    state = AgentState(task_id=task.task_id, status=TaskStatus.RUNNING)

    with pytest.raises(ValueError, match="status"):
        AgentOrchestrator(task=task, handlers=_handlers([]), initial_state=state)
