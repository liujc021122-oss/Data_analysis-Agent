from __future__ import annotations

from collections.abc import MutableSequence
from typing import Any, Protocol
from uuid import UUID

from .models import TaskMessage


class TaskBroker(Protocol):
    def enqueue(self, task_id: UUID, *, countdown: float = 0.0) -> TaskMessage:
        ...

    def revoke(
        self,
        task_id: UUID,
        *,
        terminate: bool = True,
        signal: str = "SIGTERM",
    ) -> None:
        ...


class InMemoryTaskBroker:
    """Deterministic broker used by tests and local development."""

    def __init__(self, *, enqueue_error: BaseException | None = None) -> None:
        self.messages: MutableSequence[TaskMessage] = []
        self.revocations: list[UUID] = []
        self.enqueue_calls: list[tuple[UUID, float]] = []
        self.enqueue_error = enqueue_error

    def enqueue(self, task_id: UUID, *, countdown: float = 0.0) -> TaskMessage:
        self.enqueue_calls.append((task_id, countdown))
        if self.enqueue_error is not None:
            error = self.enqueue_error
            if isinstance(error, BaseException):
                raise error
            raise RuntimeError("broker enqueue failed")
        message = TaskMessage(task_id=task_id)
        self.messages.append(message)
        return message

    def revoke(
        self,
        task_id: UUID,
        *,
        terminate: bool = True,
        signal: str = "SIGTERM",
    ) -> None:
        self.revocations.append(task_id)

    def pop(self) -> TaskMessage:
        return self.messages.pop(0)


class CeleryTaskBroker:
    """Celery adapter that keeps broker messages opaque and task-scoped."""

    def __init__(self, celery_app: Any, *, task_name: str) -> None:
        self.celery_app = celery_app
        self.task_name = task_name

    def enqueue(self, task_id: UUID, *, countdown: float = 0.0) -> TaskMessage:
        self.celery_app.send_task(
            self.task_name,
            args=[str(task_id)],
            task_id=str(task_id),
            countdown=max(0.0, float(countdown)),
        )
        return TaskMessage(task_id=task_id)

    def revoke(
        self,
        task_id: UUID,
        *,
        terminate: bool = True,
        signal: str = "SIGTERM",
    ) -> None:
        self.celery_app.control.revoke(
            str(task_id), terminate=terminate, signal=signal
        )


__all__ = ["CeleryTaskBroker", "InMemoryTaskBroker", "TaskBroker"]
