import pytest
from math import nan

from data_analysis_agent.agent.orchestration_models import AgentCheckpoint, StageFailure, StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import ReportFormat, TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask, ReportArtifact


def _handlers(trace):
    return {
        stage: lambda stage_input, call_tool, stage=stage: (trace.append(stage), StageResult())[1]
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }


def test_resume_from_exploring_does_not_run_prior_stages():
    task = AnalysisTask(query="resume")
    state = AgentState(task_id=task.task_id, status=TaskStatus.EXPLORING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.EXPLORING}),
        state=state,
        step_number=2,
        context={"profile": {"rows": 2}},
    )
    trace = []

    result = AgentOrchestrator(task=task, handlers=_handlers(trace)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert TaskStatus.RUNNING not in trace
    assert TaskStatus.EXPLORING in trace
    assert result.checkpoint.context["profile"]["rows"] == 2


def test_completed_checkpoint_returns_saved_output_without_calling_handlers():
    task = AnalysisTask(query="completed")
    state = AgentState(task_id=task.task_id, status=TaskStatus.COMPLETED)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.COMPLETED}),
        state=state,
        step_number=8,
        output={"final_report": "# saved"},
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert result.output == {"final_report": "# saved"}
    assert calls == []


def test_existing_report_artifact_skips_report_handler():
    task = AnalysisTask(query="report once")
    report = ReportArtifact(format=ReportFormat.MARKDOWN, file_path="reports/report.md")
    state = AgentState(
        task_id=task.task_id,
        status=TaskStatus.REPORTING,
        report_artifacts=(report,),
    )
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.REPORTING}),
        state=state,
        step_number=7,
        output={"final_report": "# existing"},
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert calls.count(TaskStatus.REPORTING) == 0
    assert result.output["final_report"] == "# existing"


def test_report_generated_checkpoint_skips_report_handler():
    task = AnalysisTask(query="report flag")
    state = AgentState(task_id=task.task_id, status=TaskStatus.REPORTING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.REPORTING}),
        state=state,
        step_number=7,
        report_generated=True,
        output={"final_report": "# saved"},
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert calls.count(TaskStatus.REPORTING) == 0
    assert result.checkpoint.report_generated is True


def test_non_finite_checkpoint_context_is_invalid_without_handler_calls():
    task = AnalysisTask(query="non-finite")
    state = AgentState(task_id=task.task_id, status=TaskStatus.EXPLORING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.EXPLORING}),
        state=state,
        step_number=2,
    ).model_copy(update={"context": {"value": nan}})
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_INVALID_CHECKPOINT"
    assert calls == []


def test_report_skip_precedes_exhausted_step_budget():
    task = AnalysisTask(query="report budget")
    state = AgentState(task_id=task.task_id, status=TaskStatus.REPORTING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.REPORTING}),
        state=state,
        step_number=50,
        report_generated=True,
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert calls == []


def test_failed_report_does_not_persist_output_or_report_flag():
    task = AnalysisTask(query="failed report")
    state = AgentState(task_id=task.task_id, status=TaskStatus.REPORTING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.REPORTING}),
        state=state,
        step_number=7,
    )
    handlers = _handlers([])
    handlers[TaskStatus.REPORTING] = lambda stage_input, call_tool: StageResult(
        completed=False,
        output={"final_report": "# should not persist"},
        failure=StageFailure(code="REPORT_FAILED", message="offline failure"),
    )

    result = AgentOrchestrator(task=task, handlers=handlers).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.FAILED
    assert result.output == {}
    assert result.checkpoint.output == {}
    assert result.checkpoint.report_generated is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda checkpoint: checkpoint.model_copy(update={"version": 2}),
        lambda checkpoint: checkpoint.model_copy(update={"task": AnalysisTask(query="other")}),
        lambda checkpoint: checkpoint.model_copy(
            update={"state": checkpoint.state.model_copy(update={"status": TaskStatus.CLEANING})}
        ),
    ],
)
def test_invalid_checkpoint_is_reported_without_handler_calls(mutate):
    task = AnalysisTask(query="resume validation")
    state = AgentState(task_id=task.task_id, status=TaskStatus.EXPLORING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.EXPLORING}),
        state=state,
        step_number=2,
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=mutate(checkpoint))

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_INVALID_CHECKPOINT"
    assert calls == []


@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_terminal_checkpoint_returns_without_retrying(status):
    task = AnalysisTask(query="terminal")
    state = AgentState(task_id=task.task_id, status=status)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": status}),
        state=state,
        step_number=3,
    )
    calls = []

    result = AgentOrchestrator(task=task, handlers=_handlers(calls)).run(checkpoint=checkpoint)

    assert result.status is status
    assert calls == []
