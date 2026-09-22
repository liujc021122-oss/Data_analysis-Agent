import pytest

from data_analysis_agent.agent.orchestration_models import StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask


def _handlers(trace):
    def make(stage):
        def handler(stage_input, call_tool):
            trace.append((stage, stage_input.stage, stage_input.attempt))
            return StageResult(output={"stage": stage.value})

        return handler

    return {stage: make(stage) for stage in AgentOrchestrator.ACTIVE_STAGES}


def test_orchestrator_runs_the_declared_stage_order_and_reaches_completed():
    trace = []
    task = AnalysisTask(query="offline flow")
    orchestrator = AgentOrchestrator(task=task, handlers=_handlers(trace))

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
    assert [event.to_status for event in result.state.events[:2]] == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ]


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
