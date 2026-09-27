from __future__ import annotations

from threading import RLock
from typing import Any
from uuid import UUID

from ..domain.enums import TaskStatus
from ..persistence.errors import EntityNotFoundError
from ..services.persistence import TaskPersistenceService
from .broker import TaskBroker
from .errors import TaskEnqueueError
from .models import TaskMessage, TaskSubmissionResult


class CancellationRegistry:
    """Connects persisted cancellation to the currently running agent."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._callbacks: dict[UUID, list[Any]] = {}
        self._cancelled: set[UUID] = set()

    def register(self, task_id: UUID, callback: Any | None = None) -> None:
        with self._lock:
            self._callbacks.setdefault(task_id, [])
            already_cancelled = task_id in self._cancelled
            if callback is not None:
                self._callbacks[task_id].append(callback)
        if already_cancelled and callback is not None:
            self._invoke(callback)

    def bind(self, task_id: UUID, callback: Any) -> None:
        self.register(task_id, callback)

    def request(self, task_id: UUID) -> bool:
        with self._lock:
            self._cancelled.add(task_id)
            callbacks = tuple(self._callbacks.get(task_id, ()))
        for callback in callbacks:
            self._invoke(callback)
        return bool(callbacks)

    def is_cancelled(self, task_id: UUID) -> bool:
        with self._lock:
            return task_id in self._cancelled

    def unregister(self, task_id: UUID) -> None:
        with self._lock:
            self._callbacks.pop(task_id, None)

    def clear(self, task_id: UUID) -> None:
        with self._lock:
            self._callbacks.pop(task_id, None)
            self._cancelled.discard(task_id)

    @staticmethod
    def _invoke(callback: Any) -> None:
        try:
            callback()
        except Exception:
            # Cancellation must not be blocked by an already-failing executor.
            return


class TaskSubmissionService:
    def __init__(
        self,
        *,
        persistence: TaskPersistenceService,
        broker: TaskBroker,
        cancellation_registry: CancellationRegistry | None = None,
    ) -> None:
        self.persistence = persistence
        self.broker = broker
        self.cancellation_registry = cancellation_registry or CancellationRegistry()

    def submit(self, *, user_id: UUID, request) -> TaskSubmissionResult:
        creation = self.persistence.create_task_with_result(
            user_id=user_id, request=request
        )
        if not creation.created:
            return TaskSubmissionResult(
                task=creation.task,
                message=TaskMessage(task_id=creation.task.task_id),
                created=False,
                enqueued=False,
            )

        try:
            queued, _ = self.persistence.transition_task(
                task_id=creation.task.task_id,
                target=TaskStatus.QUEUED,
                message="task queued",
            )
            message = self.broker.enqueue(queued.task_id)
        except Exception as exc:
            self.persistence.fail_task(
                creation.task.task_id,
                code="TASK_ENQUEUE_FAILED",
                message="task could not be queued",
            )
            raise TaskEnqueueError("task could not be queued") from exc
        return TaskSubmissionResult(
            task=queued,
            message=message,
            created=True,
            enqueued=True,
        )

    def cancel(self, task_id: UUID):
        current = self.persistence.get_task(task_id)
        if current is None:
            raise EntityNotFoundError(f"task {task_id} not found")
        if current.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            updated = self.persistence.cancel_task(task_id)
            try:
                self.broker.revoke(task_id, terminate=True, signal="SIGTERM")
            except Exception:
                # The database cancellation is authoritative when Redis is down.
                pass
            finally:
                self.cancellation_registry.request(task_id)
            return updated
        return current

    def cancel_for_user(self, task_id: UUID, user_id: UUID):
        updated, changed = self.persistence.cancel_task_for_user(task_id, user_id)
        if changed:
            try:
                self.broker.revoke(task_id, terminate=True, signal="SIGTERM")
            except Exception:
                pass
            finally:
                self.cancellation_registry.request(task_id)
        return updated

    def retry_for_user(self, task_id: UUID, user_id: UUID) -> TaskSubmissionResult:
        queued = self.persistence.retry_failed_task(task_id, user_id)
        try:
            message = self.broker.enqueue(task_id)
        except Exception as exc:
            self.persistence.fail_task(
                task_id, code="TASK_ENQUEUE_FAILED", message="task could not be queued"
            )
            raise TaskEnqueueError("task could not be queued") from exc
        return TaskSubmissionResult(
            task=queued, message=message, created=False, enqueued=True
        )


__all__ = ["CancellationRegistry", "TaskSubmissionService"]
