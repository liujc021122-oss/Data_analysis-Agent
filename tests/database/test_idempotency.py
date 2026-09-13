from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.models import AnalysisTask, utc_now
from data_analysis_agent.persistence.errors import IdempotencyConflictError
from data_analysis_agent.persistence.models import UserRecord
from data_analysis_agent.persistence.repositories import TaskCreationResult, TaskRepository
from data_analysis_agent.services.idempotency import compute_request_hash
from data_analysis_agent.services.persistence import TaskPersistenceService


def test_same_key_and_request_returns_one_task_and_changed_request_conflicts(uow_factory):
    user_id = uuid4()
    first = AnalysisTaskCreateRequest(query="分析销售数据", idempotency_key="same-key")
    second = AnalysisTaskCreateRequest(query="分析销售数据", idempotency_key="same-key")
    changed = AnalysisTaskCreateRequest(query="分析库存数据", idempotency_key="same-key")
    service = TaskPersistenceService(uow_factory)

    first_task = service.create_task(user_id=user_id, request=first)
    second_task = service.create_task(user_id=user_id, request=second)

    assert second_task.task_id == first_task.task_id
    with pytest.raises(IdempotencyConflictError):
        service.create_task(user_id=user_id, request=changed)


def test_request_hash_excludes_idempotency_key_but_preserves_request_fields():
    first = AnalysisTaskCreateRequest(query="分析", idempotency_key="one")
    second = AnalysisTaskCreateRequest(query="分析", idempotency_key="two")

    assert compute_request_hash(first) == compute_request_hash(second)


def test_request_hash_preserves_dataset_order():
    first_id, second_id = uuid4(), uuid4()
    first = AnalysisTaskCreateRequest(
        query="分析",
        idempotency_key="one",
        dataset_ids=(first_id, second_id),
    )
    same_order = AnalysisTaskCreateRequest(
        query="分析",
        idempotency_key="two",
        dataset_ids=(first_id, second_id),
    )
    reversed_order = AnalysisTaskCreateRequest(
        query="分析",
        idempotency_key="three",
        dataset_ids=(second_id, first_id),
    )

    assert compute_request_hash(first) == compute_request_hash(same_order)
    assert compute_request_hash(first) != compute_request_hash(reversed_order)


def test_only_the_named_idempotency_constraint_is_classified():
    named = IntegrityError(
        "insert",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(
                constraint_name="uq_analysis_tasks_user_idempotency"
            )
        ),
    )
    unrelated = IntegrityError(
        "analysis_tasks.user_id analysis_tasks.idempotency_key",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="other_unique_constraint")
        ),
    )

    assert TaskRepository._is_idempotency_conflict(named)
    assert not TaskRepository._is_idempotency_conflict(unrelated)


def test_unique_conflict_requeries_and_returns_the_winning_task(uow_factory, monkeypatch):
    user_id = uuid4()
    winner = AnalysisTask(query="分析", task_id=uuid4())
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=utc_now()))
        uow.tasks.add(
            user_id=user_id,
            task=winner,
            idempotency_key="race-key",
            request_hash="request-hash",
        )
        uow.commit()

    with uow_factory() as uow:
        persisted_winner = uow.tasks.get(winner.task_id)
        calls = iter([None, (persisted_winner, "request-hash")])
        monkeypatch.setattr(
            uow.tasks, "_get_by_idempotency", lambda user, key: next(calls)
        )
        result = uow.tasks.create_idempotent_with_result(
            user_id=user_id,
            task=AnalysisTask(query="分析", task_id=uuid4()),
            idempotency_key="race-key",
            request_hash="request-hash",
        )
        assert isinstance(result, TaskCreationResult)
        assert result.task.task_id == winner.task_id
        assert result.created is False


def test_different_users_may_reuse_the_same_key(uow_factory):
    service = TaskPersistenceService(uow_factory)
    first = service.create_task(
        user_id=uuid4(), request=AnalysisTaskCreateRequest(query="分析", idempotency_key="shared")
    )
    second = service.create_task(
        user_id=uuid4(), request=AnalysisTaskCreateRequest(query="分析", idempotency_key="shared")
    )

    assert first.task_id != second.task_id
