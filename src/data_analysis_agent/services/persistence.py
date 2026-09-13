from uuid import UUID

from ..api.schemas import AnalysisTaskCreateRequest
from ..domain.enums import TaskEventType, TaskStatus
from ..domain.models import AnalysisTask, TaskEvent, utc_now
from ..domain.state import transition_task
from ..persistence.errors import EntityNotFoundError
from ..persistence.models import UserRecord
from .idempotency import compute_request_hash


class TaskPersistenceService:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    def create_task(self, *, user_id: UUID, request: AnalysisTaskCreateRequest) -> AnalysisTask:
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
            return result.task

    def transition_task(
        self, *, task_id: UUID, target: TaskStatus, message: str | None = None
    ) -> tuple[AnalysisTask, TaskEvent]:
        with self.uow_factory() as uow:
            current = uow.tasks.get_for_update(task_id)
            if current is None:
                raise EntityNotFoundError(f"task {task_id} not found")
            updated, event = transition_task(current, target, message=message)
            updated = uow.tasks.update(updated)
            event = uow.task_events.append(event)
            uow.commit()
            return updated, event

    def record_model_call(self, *, task_id: UUID, duration_ms: int) -> AnalysisTask:
        with self.uow_factory() as uow:
            task = uow.tasks.record_model_call(
                task_id=task_id, duration_ms=duration_ms
            )
            uow.commit()
            return task
