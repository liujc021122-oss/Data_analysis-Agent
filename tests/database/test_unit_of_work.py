from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import AnalysisTask, TaskEvent
from data_analysis_agent.persistence.errors import TransactionError
from data_analysis_agent.persistence.models import UserRecord


def test_uow_commits_normal_transactions_and_closes_session(uow_factory):
    user = UserRecord(user_id=uuid4(), created_at=datetime.now(timezone.utc))
    with uow_factory() as uow:
        session = uow.session
        uow.users.ensure(user)
        uow.commit()
    assert not session.in_transaction()

    with uow_factory() as uow:
        assert uow.users.get(user.user_id) == user


def test_uow_rolls_back_exception_and_closes_session(uow_factory):
    user = UserRecord(user_id=uuid4(), created_at=datetime.now(timezone.utc))
    with pytest.raises(RuntimeError):
        with uow_factory() as uow:
            session = uow.session
            uow.users.ensure(user)
            raise RuntimeError("boom")
    assert not session.in_transaction()
    with uow_factory() as uow:
        assert uow.users.get(user.user_id) is None


def test_uow_translates_commit_and_rollback_sqlalchemy_failures(uow_factory, monkeypatch):
    with uow_factory() as uow:
        monkeypatch.setattr(uow.session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("x")))
        with pytest.raises(TransactionError, match="commit"):
            uow.commit()

    with uow_factory() as uow:
        monkeypatch.setattr(uow.session, "rollback", lambda: (_ for _ in ()).throw(SQLAlchemyError("x")))
        with pytest.raises(TransactionError, match="rollback"):
            uow.rollback()


def test_uow_commit_preserves_commit_failure_when_rollback_also_fails(uow_factory, monkeypatch):
    uow = uow_factory()
    try:
        monkeypatch.setattr(uow.session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("commit failure")))
        monkeypatch.setattr(uow.session, "rollback", lambda: (_ for _ in ()).throw(SQLAlchemyError("rollback failure")))
        with pytest.raises(TransactionError, match="commit") as error:
            uow.commit()
        assert isinstance(error.value.__cause__, SQLAlchemyError)
    finally:
        uow.session.close()


def test_uow_exposes_future_repository_slots(uow_factory):
    with uow_factory() as uow:
        assert all(hasattr(uow, name) for name in ("users", "datasets", "tasks", "task_events", "tool_calls", "executions", "artifacts", "reports"))
