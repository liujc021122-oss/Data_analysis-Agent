from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from ..domain.enums import TaskStatus
from ..domain.models import AnalysisTask


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskMessage(WorkerModel):
    """The only payload sent through the broker."""

    task_id: UUID


class TaskSubmissionResult(WorkerModel):
    task: AnalysisTask
    message: TaskMessage
    created: StrictBool
    enqueued: StrictBool

    @property
    def task_id(self) -> UUID:
        return self.task.task_id


class WorkerResult(WorkerModel):
    task_id: UUID
    status: TaskStatus
    executed: StrictBool
    retry_scheduled: StrictBool = False
    attempt: StrictInt = Field(default=0, ge=0)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    duration_seconds: StrictFloat | None = Field(default=None, ge=0)


__all__ = [
    "TaskMessage",
    "TaskSubmissionResult",
    "WorkerResult",
]
