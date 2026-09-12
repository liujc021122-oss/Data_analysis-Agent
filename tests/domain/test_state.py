from datetime import datetime, timezone

import pytest

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.errors import InvalidStatusTransitionError
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.domain.state import (
    LEGAL_STATUS_TRANSITIONS,
    can_transition,
    transition_status,
    transition_task,
)


def test_legal_transition_matrix_contains_the_declared_lifecycle():
    assert LEGAL_STATUS_TRANSITIONS[TaskStatus.PENDING] == frozenset(
        {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    )
    assert LEGAL_STATUS_TRANSITIONS[TaskStatus.REPORTING] == frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    )
    for terminal in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        assert LEGAL_STATUS_TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.EXPLORING),
        (TaskStatus.EXPLORING, TaskStatus.CLEANING),
        (TaskStatus.CLEANING, TaskStatus.ANALYZING),
        (TaskStatus.ANALYZING, TaskStatus.VALIDATING),
        (TaskStatus.VALIDATING, TaskStatus.REPORTING),
        (TaskStatus.REPORTING, TaskStatus.COMPLETED),
    ],
)
def test_declared_lifecycle_transitions_are_allowed(current, target):
    assert can_transition(current, target) is True
    assert transition_status(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
        (TaskStatus.FAILED, TaskStatus.QUEUED),
        (TaskStatus.CANCELLED, TaskStatus.PENDING),
    ],
)
def test_illegal_transitions_raise_a_specific_domain_error(current, target):
    assert can_transition(current, target) is False

    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        transition_status(current, target)

    assert exc_info.value.current_status is current
    assert exc_info.value.target_status is target


def test_transition_task_returns_a_new_task_and_status_event():
    occurred_at = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    task = AnalysisTask(query="分析样例", status=TaskStatus.PENDING)

    updated, event = transition_task(
        task,
        TaskStatus.QUEUED,
        message="queued by test",
        occurred_at=occurred_at,
    )

    assert updated is not task
    assert task.status is TaskStatus.PENDING
    assert updated.status is TaskStatus.QUEUED
    assert updated.updated_at == occurred_at
    assert event.task_id == task.task_id
    assert event.event_type is TaskEventType.STATUS_CHANGED
    assert event.from_status is TaskStatus.PENDING
    assert event.to_status is TaskStatus.QUEUED
    assert event.message == "queued by test"
    assert event.occurred_at == occurred_at
