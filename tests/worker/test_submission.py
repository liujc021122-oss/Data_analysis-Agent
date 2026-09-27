from uuid import uuid4

import pytest

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.persistence.errors import IdempotencyConflictError
from data_analysis_agent.services.persistence import TaskPersistenceService
from data_analysis_agent.worker import (
    CancellationRegistry,
    InMemoryTaskBroker,
    TaskEnqueueError,
    TaskSubmissionService,
)


def _request(key: str, query: str = "分析销售数据"):
    return AnalysisTaskCreateRequest(query=query, idempotency_key=key)


def test_submit_enqueues_new_task_once_and_duplicate_returns_same_task(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    broker = InMemoryTaskBroker()
    service = TaskSubmissionService(persistence=persistence, broker=broker)
    user_id = uuid4()

    first = service.submit(user_id=user_id, request=_request("same-key"))
    second = service.submit(user_id=user_id, request=_request("same-key"))

    assert first.task.task_id == second.task.task_id
    assert first.task.status is TaskStatus.QUEUED
    assert first.created is True
    assert second.created is False
    assert second.enqueued is False
    assert broker.messages == [first.message]


def test_enqueue_failure_is_persisted_as_failed_task(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    broker = InMemoryTaskBroker(enqueue_error=RuntimeError("broker unavailable"))
    service = TaskSubmissionService(persistence=persistence, broker=broker)
    user_id = uuid4()

    with pytest.raises(TaskEnqueueError):
        service.submit(user_id=user_id, request=_request("enqueue-error"))

    with uow_factory() as uow:
        task = uow.tasks.get(
            persistence.get_task_by_idempotency(user_id, "enqueue-error").task_id
        )
    assert task.status is TaskStatus.FAILED
    assert task.error_code == "TASK_ENQUEUE_FAILED"
    assert task.error_message == "task could not be queued"


def test_different_request_with_same_key_is_rejected_without_new_message(
    uow_factory,
):
    persistence = TaskPersistenceService(uow_factory)
    broker = InMemoryTaskBroker()
    service = TaskSubmissionService(persistence=persistence, broker=broker)
    user_id = uuid4()

    service.submit(user_id=user_id, request=_request("conflict", "first"))
    with pytest.raises(IdempotencyConflictError):
        service.submit(user_id=user_id, request=_request("conflict", "second"))

    assert len(broker.messages) == 1


def test_cancel_revokes_queued_message_and_is_idempotent(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    broker = InMemoryTaskBroker()
    cancellation = CancellationRegistry()
    service = TaskSubmissionService(
        persistence=persistence,
        broker=broker,
        cancellation_registry=cancellation,
    )
    result = service.submit(user_id=uuid4(), request=_request("cancel"))

    cancelled = service.cancel(result.task.task_id)
    repeated = service.cancel(result.task.task_id)

    assert cancelled.status is TaskStatus.CANCELLED
    assert repeated.status is TaskStatus.CANCELLED
    assert broker.revocations == [result.task.task_id]
    assert cancellation.is_cancelled(result.task.task_id)
