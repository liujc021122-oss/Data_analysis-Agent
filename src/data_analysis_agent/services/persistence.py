from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from ..api.schemas import AnalysisTaskCreateRequest
from ..domain.enums import TaskEventType, TaskStatus
from ..domain.models import AnalysisTask, TaskEvent, utc_now
from ..domain.state import transition_task
from ..persistence.errors import EntityNotFoundError
from ..persistence.models import UserRecord
from ..persistence.repositories import TaskCreationResult
from .idempotency import compute_request_hash


@dataclass(frozen=True)
class TaskClaimResult:
    task: AnalysisTask
    claimed: bool

    def __iter__(self):
        yield self.task
        yield self.claimed


class TaskRetryConflictError(ValueError):
    """Manual retry is only available for a failed task."""


class TaskPersistenceService:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    def create_task(
        self, *, user_id: UUID, request: AnalysisTaskCreateRequest
    ) -> AnalysisTask:
        return self.create_task_with_result(user_id=user_id, request=request).task

    def create_task_with_result(
        self, *, user_id: UUID, request: AnalysisTaskCreateRequest
    ) -> TaskCreationResult:
        request_hash = compute_request_hash(request)
        task = AnalysisTask(
            query=request.query,
            dataset_ids=request.dataset_ids,
            max_rounds=request.max_rounds,
            metadata=request.metadata,
        )
        with self.uow_factory() as uow:
            uow.users.ensure(UserRecord(user_id=user_id, created_at=utc_now()))
            result = uow.tasks.create_idempotent_with_result(
                user_id=user_id,
                task=task,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
            if result.created:
                for dataset_id in request.dataset_ids:
                    if uow.datasets.get_for_user(dataset_id, user_id) is None:
                        raise EntityNotFoundError(f"dataset {dataset_id} not found")
                for position, dataset_id in enumerate(request.dataset_ids):
                    uow.tasks.attach_dataset(
                        task_id=result.task.task_id,
                        dataset_id=dataset_id,
                        position=position,
                    )
                uow.task_events.append(
                    TaskEvent(
                        task_id=result.task.task_id,
                        event_type=TaskEventType.STATUS_CHANGED,
                        from_status=None,
                        to_status=TaskStatus.PENDING,
                        message="task created",
                    )
                )
            uow.commit()
            return result

    def get_task(self, task_id: UUID) -> AnalysisTask | None:
        with self.uow_factory() as uow:
            return uow.tasks.get(task_id)

    def get_task_for_user(self, task_id: UUID, user_id: UUID) -> AnalysisTask | None:
        with self.uow_factory() as uow:
            return uow.tasks.get_for_user(task_id, user_id)

    def list_tasks_for_user(
        self, user_id: UUID, status: TaskStatus | None, offset: int, limit: int
    ) -> tuple[list[AnalysisTask], int]:
        with self.uow_factory() as uow:
            return uow.tasks.list_for_user(user_id, status=status, offset=offset, limit=limit)

    def list_events_for_user(
        self, task_id: UUID, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[TaskEvent], int]:
        with self.uow_factory() as uow:
            if uow.tasks.get_for_user(task_id, user_id) is None:
                raise EntityNotFoundError("task is unavailable")
            return uow.task_events.page_for_task(task_id, offset=offset, limit=limit)

    def list_artifacts_for_user(self, task_id: UUID, user_id: UUID):
        with self.uow_factory() as uow:
            if uow.tasks.get_for_user(task_id, user_id) is None:
                raise EntityNotFoundError("task is unavailable")
            return uow.artifacts.list_for_task(task_id)

    def retry_failed_task(self, task_id: UUID, user_id: UUID) -> AnalysisTask:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_user(task_id, user_id, for_update=True)
            if current is None:
                raise EntityNotFoundError("task is unavailable")
            if current.status is not TaskStatus.FAILED:
                raise TaskRetryConflictError("task is not failed")
            retry_input = current.model_copy(update={"error_code": None, "error_message": None})
            updated, event = transition_task(
                retry_input, TaskStatus.QUEUED,
                message="task manually retried",
                occurred_at=self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(task_id),
                ),
            )
            if not uow.tasks.update_if_status(updated, expected=TaskStatus.FAILED):
                raise TaskRetryConflictError("task is not failed")
            uow.task_events.append(event)
            uow.commit()
            return updated

    def cancel_task_for_user(self, task_id: UUID, user_id: UUID) -> tuple[AnalysisTask, bool]:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_user(task_id, user_id, for_update=True)
            if current is None:
                raise EntityNotFoundError("task is unavailable")
            if current.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return current, False
            cancelled = current.model_copy(update={
                "error_code": "TASK_CANCELLED", "error_message": "task cancelled"
            })
            updated, event = transition_task(
                cancelled, TaskStatus.CANCELLED, message="task cancelled",
                occurred_at=self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(task_id),
                ),
            )
            event = event.model_copy(update={"metadata": {"error_code": "TASK_CANCELLED"}})
            if not uow.tasks.update_if_status(updated, expected=current.status):
                return uow.tasks.get(task_id), False
            uow.task_events.append(event)
            uow.commit()
            return updated, True

    def get_task_by_idempotency(
        self, user_id: UUID, idempotency_key: str
    ) -> AnalysisTask | None:
        with self.uow_factory() as uow:
            return uow.tasks.get_by_idempotency(user_id, idempotency_key)

    def get_task_owner_id(self, task_id: UUID) -> UUID | None:
        with self.uow_factory() as uow:
            return uow.tasks.get_owner_id(task_id)

    def transition_task(
        self,
        *,
        task_id: UUID,
        target: TaskStatus,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> tuple[AnalysisTask, TaskEvent]:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_update(task_id)
            if current is None:
                raise EntityNotFoundError(f"task {task_id} not found")
            event_time = self._next_event_time(
                current,
                occurred_at,
                uow.task_events.latest_occurred_at(task_id),
            )
            updated, event = transition_task(
                current,
                target,
                message=message,
                occurred_at=event_time,
            )
            if metadata:
                event = event.model_copy(update={"metadata": dict(metadata)})
            updated = uow.tasks.update(updated)
            event = uow.task_events.append(event)
            uow.commit()
            return updated, event

    def claim_task(self, task_id: UUID) -> TaskClaimResult:
        with self.uow_factory() as uow:
            claimed_task = uow.tasks.claim_queued(task_id)
            if claimed_task is None:
                current = uow.tasks.get_for_update(task_id)
                if current is None:
                    raise EntityNotFoundError(f"task {task_id} not found")
                return TaskClaimResult(task=current, claimed=False)
            current = claimed_task.model_copy(update={"status": TaskStatus.QUEUED})
            now = self._next_event_time(
                current,
                None,
                uow.task_events.latest_occurred_at(task_id),
            )
            updated, event = transition_task(
                current,
                TaskStatus.RUNNING,
                message="worker claimed task",
                occurred_at=now,
            )
            event = event.model_copy(
                update={
                    "metadata": {
                        "worker_claimed": True,
                        "phase_started_at": now.isoformat(),
                        "phase_finished_at": now.isoformat(),
                        "completed_phase": TaskStatus.QUEUED.value,
                    }
                }
            )
            updated = uow.tasks.update(updated)
            uow.task_events.append(event)
            uow.commit()
            return TaskClaimResult(task=updated, claimed=True)

    def record_stage_transition(
        self,
        *,
        task_id: UUID,
        target: TaskStatus,
        message: str | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AnalysisTask:
        current = self.get_task(task_id)
        if current is None:
            raise EntityNotFoundError(f"task {task_id} not found")
        if current.status is target or current.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return current
        updated, _ = self.transition_task(
            task_id=task_id,
            target=target,
            message=message,
            metadata=metadata,
            occurred_at=occurred_at,
        )
        return updated

    def fail_task(
        self,
        task_id: UUID,
        *,
        code: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> AnalysisTask:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_update(task_id)
            if current is None:
                raise EntityNotFoundError(f"task {task_id} not found")
            if current.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return current
            failed_input = current.model_copy(
                update={"error_code": code, "error_message": message}
            )
            updated, event = transition_task(
                failed_input,
                TaskStatus.FAILED,
                message=message,
                occurred_at=self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(task_id),
                ),
            )
            event = event.model_copy(
                update={
                    "metadata": {
                        "error_code": code,
                        **(metadata or {}),
                    }
                }
            )
            updated = uow.tasks.update(updated)
            uow.task_events.append(event)
            uow.commit()
            return updated

    def cancel_task(
        self,
        task_id: UUID,
        *,
        code: str = "TASK_CANCELLED",
        message: str = "task cancelled",
    ) -> AnalysisTask:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_update(task_id)
            if current is None:
                raise EntityNotFoundError(f"task {task_id} not found")
            if current.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return current
            cancelled_input = current.model_copy(
                update={"error_code": code, "error_message": message}
            )
            updated, event = transition_task(
                cancelled_input,
                TaskStatus.CANCELLED,
                message=message,
                occurred_at=self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(task_id),
                ),
            )
            event = event.model_copy(update={"metadata": {"error_code": code}})
            updated = uow.tasks.update(updated)
            uow.task_events.append(event)
            uow.commit()
            return updated

    def requeue_for_retry(
        self,
        task_id: UUID,
        *,
        code: str,
        message: str,
    ) -> AnalysisTask:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_update(task_id)
            if current is None:
                raise EntityNotFoundError(f"task {task_id} not found")
            if current.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return current
            attempt = int(current.metadata.get("worker_attempt", 0)) + 1
            retry_input = current.model_copy(
                update={
                    "metadata": {
                        **current.metadata,
                        "worker_attempt": attempt,
                        "last_worker_error_code": code,
                    },
                    "error_code": None,
                    "error_message": None,
                }
            )
            updated, event = transition_task(
                retry_input,
                TaskStatus.QUEUED,
                message="task queued for retry",
                occurred_at=self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(task_id),
                ),
            )
            event = event.model_copy(
                update={
                    "metadata": {
                        "retry": True,
                        "attempt": attempt,
                        "error_code": code,
                        "error_message": message,
                    }
                }
            )
            updated = uow.tasks.update(updated)
            uow.task_events.append(event)
            uow.commit()
            return updated

    def recover_stale_tasks(
        self,
        *,
        before: datetime,
        statuses: tuple[TaskStatus, ...] = (
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        ),
    ) -> list[AnalysisTask]:
        with self.uow_factory() as uow:
            candidates: list[AnalysisTask] = []
            seen: set[UUID] = set()
            for status in statuses:
                for candidate in uow.tasks.list_status_before(
                    status=status, before=before
                ):
                    if candidate.task_id not in seen:
                        candidates.append(candidate)
                        seen.add(candidate.task_id)
        recovered: list[AnalysisTask] = []
        for candidate in candidates:
            with self.uow_factory() as uow:
                current = uow.tasks.get_for_update(candidate.task_id)
                if (
                    current is None
                    or current.status not in statuses
                    or current.updated_at >= before
                ):
                    continue
                recovered_at = self._next_event_time(
                    current,
                    None,
                    uow.task_events.latest_occurred_at(candidate.task_id),
                )
                recovery_metadata = {
                    "worker_recovered": True,
                    "phase_finished_at": recovered_at.isoformat(),
                    "recovered_at": recovered_at.isoformat(),
                }
                event = None
                if current.status is TaskStatus.QUEUED:
                    # Refresh the lease before publishing. If the worker dies
                    # before publish, a later recovery pass can retry it.
                    updated = current.model_copy(
                        update={
                            "updated_at": recovered_at,
                            "metadata": {
                                **current.metadata,
                                **recovery_metadata,
                            },
                        }
                    )
                else:
                    updated, event = transition_task(
                        current,
                        TaskStatus.QUEUED,
                        message="task recovered after worker restart",
                        occurred_at=recovered_at,
                    )
                    event = event.model_copy(
                        update={"metadata": recovery_metadata}
                    )
                updated = uow.tasks.update(updated)
                if event is not None:
                    uow.task_events.append(event)
                uow.commit()
                recovered.append(updated)
        return recovered

    @staticmethod
    def _next_event_time(
        task: AnalysisTask,
        requested: datetime | None,
        latest_event_at: datetime | None = None,
    ) -> datetime:
        candidate = requested or utc_now()
        floor = max(task.updated_at, latest_event_at or task.updated_at)
        if candidate <= floor:
            return floor + timedelta(microseconds=1)
        return candidate

    def record_model_call(self, *, task_id: UUID, duration_ms: int) -> AnalysisTask:
        with self.uow_factory() as uow:
            task = uow.tasks.record_model_call(
                task_id=task_id, duration_ms=duration_ms
            )
            uow.commit()
            return task
