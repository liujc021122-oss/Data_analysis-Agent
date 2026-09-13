from uuid import uuid4

import pytest
from sqlalchemy import select

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.errors import InvalidStatusTransitionError
from data_analysis_agent.persistence.errors import EntityNotFoundError
from data_analysis_agent.persistence.models import DatasetRecord
from data_analysis_agent.persistence.orm_models import AnalysisTaskORM, TaskEventORM
from data_analysis_agent.services.persistence import TaskPersistenceService


def _dataset(user_id, dataset_id):
    return DatasetRecord(
        dataset_id=dataset_id,
        user_id=user_id,
        name="sample.csv",
        source_uri="s3://bucket/sample.csv",
        size_bytes=3,
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


def _request(key, query="分析销售数据", dataset_ids=()):
    return AnalysisTaskCreateRequest(
        query=query, idempotency_key=key, dataset_ids=dataset_ids
    )


def test_idempotent_creation_has_one_association_and_initial_event(uow_factory):
    user_id, dataset_id = uuid4(), uuid4()
    with uow_factory() as uow:
        uow.users.ensure(__import__("data_analysis_agent.persistence.models", fromlist=["UserRecord"]).UserRecord(user_id=user_id, created_at=__import__("data_analysis_agent.domain.models", fromlist=["utc_now"]).utc_now()))
        uow.datasets.add(_dataset(user_id, dataset_id))
        uow.commit()
    service = TaskPersistenceService(uow_factory)

    first = service.create_task(user_id=user_id, request=_request("key", dataset_ids=(dataset_id,)))
    second = service.create_task(user_id=user_id, request=_request("key", dataset_ids=(dataset_id,)))

    assert second.task_id == first.task_id
    assert first.dataset_ids == (dataset_id,)
    with uow_factory() as uow:
        assert uow.tasks.get(first.task_id).dataset_ids == (dataset_id,)
        events = uow.task_events.list_for_task(first.task_id)
        assert len(events) == 1
        assert events[0].to_status is TaskStatus.PENDING


def test_missing_dataset_rolls_back_user_and_task(uow_factory):
    user_id = uuid4()
    with pytest.raises(EntityNotFoundError):
        TaskPersistenceService(uow_factory).create_task(
            user_id=user_id, request=_request("missing", dataset_ids=(uuid4(),))
        )
    with uow_factory() as uow:
        assert uow.users.get(user_id) is None


def test_event_failure_rolls_back_task_and_initial_event(uow_factory, monkeypatch):
    def fail_append(self, event):
        raise RuntimeError("event write failed")

    monkeypatch.setattr("data_analysis_agent.persistence.repositories.TaskEventRepository.append", fail_append)
    user_id = uuid4()
    with pytest.raises(RuntimeError):
        TaskPersistenceService(uow_factory).create_task(
            user_id=user_id, request=_request("rollback")
        )
    with uow_factory() as uow:
        assert uow.users.get(user_id) is None
        assert uow.session.scalars(select(AnalysisTaskORM)).all() == []
        assert uow.session.scalars(select(TaskEventORM)).all() == []


def test_transition_uses_domain_rules_and_persists_event(uow_factory):
    service = TaskPersistenceService(uow_factory)
    task = service.create_task(user_id=uuid4(), request=_request("transition"))

    updated, event = service.transition_task(
        task_id=task.task_id, target=TaskStatus.QUEUED, message="queued"
    )

    assert updated.status is TaskStatus.QUEUED
    assert event.from_status is TaskStatus.PENDING
    with uow_factory() as uow:
        assert len(uow.task_events.list_for_task(task.task_id)) == 2


def test_invalid_transition_leaves_status_and_event_count_unchanged(uow_factory):
    service = TaskPersistenceService(uow_factory)
    task = service.create_task(user_id=uuid4(), request=_request("invalid"))

    with pytest.raises(InvalidStatusTransitionError):
        service.transition_task(task_id=task.task_id, target=TaskStatus.COMPLETED)
    with uow_factory() as uow:
        assert uow.tasks.get(task.task_id).status is TaskStatus.PENDING
        assert len(uow.task_events.list_for_task(task.task_id)) == 1


def test_model_call_statistics_accumulate_and_reject_negative_duration(uow_factory):
    service = TaskPersistenceService(uow_factory)
    task = service.create_task(user_id=uuid4(), request=_request("stats"))

    for duration in (10, 25, 5):
        task = service.record_model_call(task_id=task.task_id, duration_ms=duration)
    with pytest.raises(ValueError):
        service.record_model_call(task_id=task.task_id, duration_ms=-1)

    with uow_factory() as uow:
        restored = uow.tasks.get(task.task_id)
        assert restored.model_call_count == 3
        assert restored.model_duration_ms == 40
