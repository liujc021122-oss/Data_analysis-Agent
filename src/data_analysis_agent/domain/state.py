from datetime import datetime
from typing import Mapping

from .enums import TaskEventType, TaskStatus
from .errors import InvalidStatusTransitionError
from .models import AnalysisTask, TaskEvent, utc_now


LEGAL_STATUS_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.EXPLORING,
            TaskStatus.CLEANING,
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.REPORTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.EXPLORING: frozenset(
        {
            TaskStatus.CLEANING,
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.CLEANING: frozenset(
        {
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.ANALYZING: frozenset(
        {
            TaskStatus.EXPLORING,
            TaskStatus.CLEANING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.VALIDATING: frozenset(
        {
            TaskStatus.ANALYZING,
            TaskStatus.REPORTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.REPORTING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def _validate_status(value: object, *, name: str) -> None:
    if not isinstance(value, TaskStatus):
        raise TypeError(f"{name} must be a TaskStatus, got {value!r}")


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    _validate_status(current, name="current")
    _validate_status(target, name="target")
    return target in LEGAL_STATUS_TRANSITIONS[current]


def transition_status(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if not can_transition(current, target):
        raise InvalidStatusTransitionError(current, target)
    return target


def transition_task(
    task: AnalysisTask,
    target: TaskStatus,
    *,
    message: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[AnalysisTask, TaskEvent]:
    transition_status(task.status, target)
    event_time = occurred_at or utc_now()
    updated_data = task.model_dump()
    updated_data["status"] = target
    updated_data["updated_at"] = event_time
    updated_task = AnalysisTask.model_validate(updated_data)
    event = TaskEvent(
        task_id=task.task_id,
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=task.status,
        to_status=target,
        message=message,
        occurred_at=event_time,
    )
    return updated_task, event
