from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.agent.orchestration_models import (
    AgentCheckpoint,
    OrchestratorLimits,
    StageFailure,
    StageInput,
    StageResult,
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
