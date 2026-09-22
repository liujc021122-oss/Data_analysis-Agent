from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.agent.orchestration_models import (
    AgentCheckpoint,
    OrchestratorLimits,
    StageFailure,
    StageInput,
    StageResult,
    OrchestrationResult,
)
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask


def test_stage_result_rejects_a_failed_result_marked_completed():
    with pytest.raises(ValidationError, match="failed stage result"):
        StageResult(
            completed=True,
            failure=StageFailure(code="MODEL_ERROR", message="offline failure"),
        )


def test_checkpoint_round_trips_to_json_with_task_and_state_statuses():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id, status=TaskStatus.PENDING)
    checkpoint = AgentCheckpoint(
        task=task,
        state=state,
        step_number=0,
        context={"dataset_id": str(uuid4())},
        output={"report": "# offline"},
    )

    restored = AgentCheckpoint.model_validate_json(checkpoint.model_dump_json())

    assert restored.task.task_id == task.task_id
    assert restored.state.status is TaskStatus.PENDING
    assert restored.output == {"report": "# offline"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_steps", 0),
        ("max_model_calls", 0),
        ("max_runtime_seconds", 0),
        ("max_stage_retries", -1),
    ],
)
def test_orchestrator_limits_reject_non_positive_values(field, value):
    with pytest.raises(ValidationError):
        OrchestratorLimits(**{field: value})


def test_stage_input_rejects_negative_step_and_model_budget():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)

    with pytest.raises(ValidationError):
        StageInput(
            task=task,
            state=state,
            stage=TaskStatus.RUNNING,
            step_number=-1,
            attempt=0,
            remaining_model_calls=1,
        )

    with pytest.raises(ValidationError):
        StageInput(
            task=task,
            state=state,
            stage=TaskStatus.RUNNING,
            step_number=0,
            attempt=0,
            remaining_model_calls=-1,
        )


def test_extra_orchestration_fields_are_rejected():
    with pytest.raises(ValidationError, match="extra_field"):
        OrchestratorLimits(extra_field=1)


def test_checkpoint_rejects_negative_stage_attempts():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)

    with pytest.raises(ValidationError):
        AgentCheckpoint(task=task, state=state, step_number=0, stage_attempts={"RUNNING": -1})


def test_models_dump_nested_data_in_json_mode_and_reject_non_json_payload():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)
    checkpoint = AgentCheckpoint(task=task, state=state, step_number=0)

    dumped = checkpoint.model_dump(mode="json")

    assert dumped["task"]["task_id"] == str(task.task_id)
    with pytest.raises(ValidationError):
        AgentCheckpoint(task=task, state=state, step_number=0, context={"bad": {1, 2}})


def test_orchestration_result_requires_errors_only_for_failed_or_cancelled():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)
    checkpoint = AgentCheckpoint(task=task, state=state, step_number=0)

    with pytest.raises(ValidationError):
        OrchestrationResult(task_id=task.task_id, status=TaskStatus.FAILED, state=state, checkpoint=checkpoint)
    with pytest.raises(ValidationError):
        OrchestrationResult(
            task_id=task.task_id, status=TaskStatus.CANCELLED, state=state,
            error_code="CANCELLED", checkpoint=checkpoint,
        )
    with pytest.raises(ValidationError):
        OrchestrationResult(
            task_id=task.task_id, status=TaskStatus.COMPLETED, state=state,
            error_code="ERROR", error_message="bad", checkpoint=checkpoint,
        )


@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_orchestration_result_accepts_valid_terminal_error(status):
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)
    checkpoint = AgentCheckpoint(task=task, state=state, step_number=0)

    result = OrchestrationResult(
        task_id=task.task_id, status=status, state=state,
        error_code="SAFE_ERROR", error_message="safe message", checkpoint=checkpoint,
    )

    assert result.status is status


def test_orchestration_result_accepts_valid_completed_result():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)
    checkpoint = AgentCheckpoint(task=task, state=state, step_number=0)

    result = OrchestrationResult(task_id=task.task_id, status=TaskStatus.COMPLETED, state=state, checkpoint=checkpoint)

    assert result.error_code is None
