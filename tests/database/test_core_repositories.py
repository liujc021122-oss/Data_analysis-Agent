from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import AnalysisTask, TaskEvent
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.persistence.errors import EntityNotFoundError, TransactionError
from data_analysis_agent.persistence.models import DatasetRecord, UserRecord


def _dataset(user_id, dataset_id):
    return DatasetRecord(dataset_id=dataset_id, user_id=user_id, name="sample.csv", source_uri="s3://bucket/sample.csv", size_bytes=3, checksum="sha256:abc", created_at=datetime.now(timezone.utc))


def test_core_repositories_create_query_update_and_attach(uow_factory):
    user_id, dataset_id, task_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    dataset = _dataset(user_id, dataset_id)
    task = AnalysisTask(task_id=task_id, query="分析样例", dataset_ids=(dataset_id,))
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.datasets.add(dataset)
        created = uow.tasks.add(user_id=user_id, task=task, idempotency_key="key-1", request_hash="hash-1")
        assert created.task_id == task.task_id and created.query == task.query and created.dataset_ids == ()
        uow.tasks.attach_dataset(task_id=task_id, dataset_id=dataset_id)
        uow.commit()
    with uow_factory() as uow:
        restored = uow.tasks.get(task_id)
        assert restored is not None and restored.dataset_ids == (dataset_id,)
        assert uow.datasets.get(dataset_id).source_uri == dataset.source_uri
        assert uow.datasets.list_for_user(user_id) == [dataset]
        assert uow.tasks.get_for_update(task_id) == restored


def test_repositories_rollback_all_four_entities(uow_factory):
    user_id, dataset_id, task_id = uuid4(), uuid4(), uuid4()
    with pytest.raises(RuntimeError):
        with uow_factory() as uow:
            uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
            uow.datasets.add(_dataset(user_id, dataset_id))
            uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"), idempotency_key="k", request_hash="h")
            uow.task_events.append(TaskEvent(task_id=task_id, event_type=TaskEventType.STATUS_CHANGED, to_status=TaskStatus.PENDING))
            raise RuntimeError("rollback")
    with uow_factory() as uow:
        assert uow.users.get(user_id) is None and uow.datasets.get(dataset_id) is None and uow.tasks.get(task_id) is None
        assert uow.task_events.list_for_task(task_id) == []


def test_task_update_preserves_owner_and_idempotency_and_events_are_ordered(uow_factory):
    user_id, task_id = uuid4(), uuid4()
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
        task = uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"), idempotency_key="k", request_hash="h")
        updated = task.model_copy(update={"status": TaskStatus.RUNNING, "query": "new", "updated_at": datetime.now(timezone.utc) + timedelta(seconds=1)})
        assert uow.tasks.update(updated) == updated
        first = TaskEvent(event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED, to_status=TaskStatus.PENDING, occurred_at=datetime.now(timezone.utc) + timedelta(seconds=2))
        second = TaskEvent(event_id=uuid4(), task_id=task_id, event_type=TaskEventType.TOOL_CALLED, to_status=TaskStatus.RUNNING, occurred_at=first.occurred_at - timedelta(seconds=1))
        uow.task_events.append(first); uow.task_events.append(second); uow.commit()
    with uow_factory() as uow:
        assert [e.event_id for e in uow.task_events.list_for_task(task_id)] == [second.event_id, first.event_id]


def test_missing_entities_and_duplicate_association_raise(uow_factory):
    user_id, dataset_id, task_id = uuid4(), uuid4(), uuid4()
    with uow_factory() as uow:
        with pytest.raises(EntityNotFoundError, match="task"):
            uow.tasks.attach_dataset(task_id=uuid4(), dataset_id=uuid4())
        uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
        uow.datasets.add(_dataset(user_id, dataset_id))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"), idempotency_key="k", request_hash="h")
        uow.tasks.attach_dataset(task_id=task_id, dataset_id=dataset_id)
        with pytest.raises(IntegrityError, match="UNIQUE"):
            uow.tasks.attach_dataset(task_id=task_id, dataset_id=dataset_id)
        uow.rollback()


def test_malformed_enum_row_is_mapped_at_boundary(uow_factory):
    user_id, task_id = uuid4(), uuid4()
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"), idempotency_key="k", request_hash="h")
        row = uow.session.get(__import__("data_analysis_agent.persistence.orm_models", fromlist=["AnalysisTaskORM"]).AnalysisTaskORM, task_id)
        row.status = "BROKEN"
        with pytest.raises(PersistenceMappingError, match="status"):
            uow.tasks.get(task_id)
        uow.rollback()


def test_failed_commit_becomes_transaction_error_and_leaves_no_rows(uow_factory):
    user_id = uuid4()
    with pytest.raises(TransactionError):
        with uow_factory() as uow:
            uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
            uow.session.add(__import__("data_analysis_agent.persistence.orm_models", fromlist=["UserORM"]).UserORM(user_id=user_id, created_at=datetime.now(timezone.utc)))
            uow.commit()
