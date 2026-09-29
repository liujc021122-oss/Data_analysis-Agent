from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.state import can_transition
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.worker.broker import InMemoryTaskBroker


@pytest.fixture
def task_api(tmp_path):
    settings = load_settings(app_env="test", environ={
        "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{tmp_path / 'tasks.sqlite3'}",
        "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
    })
    app_services = APIApplication.from_settings(settings)
    init_database(app_services.database.engine)
    broker = InMemoryTaskBroker()
    owner, stranger = uuid4(), uuid4()
    app_services.configure_task_services(broker)
    app_services.principal_provider = HeaderPrincipalProvider()
    app = create_app(app_services)
    try:
        yield app_services, broker, TestClient(app, headers={"X-User-ID": str(owner)}), TestClient(app, headers={"X-User-ID": str(stranger)}), owner
    finally:
        app_services.database.engine.dispose()


def _created(client, key="task-key"):
    response = client.post("/api/analysis-tasks", json={"query": "分析销售", "idempotency_key": key})
    assert response.status_code == 202, response.text
    return response


def test_failed_task_can_transition_to_queued():
    assert can_transition(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_create_is_idempotent_and_owner_scoped(task_api):
    _services, broker, owner, stranger, _ = task_api
    first = _created(owner)
    second = _created(owner)
    task_id = first.json()["task_id"]
    assert second.json()["task_id"] == task_id
    assert second.json()["created"] is False
    assert [str(message.task_id) for message in broker.messages] == [task_id]
    detail = owner.get(f"/api/analysis-tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "QUEUED"
    assert owner.get("/api/analysis-tasks").json()["total"] == 1
    assert stranger.get("/api/analysis-tasks").json()["total"] == 0
    hidden = stranger.get(f"/api/analysis-tasks/{task_id}")
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "TASK_NOT_FOUND"
    assert hidden.json()["request_id"] == hidden.headers["X-Request-ID"]


def test_owner_events_and_cancel_are_idempotent(task_api):
    _services, broker, owner, stranger, _ = task_api
    task_id = _created(owner).json()["task_id"]
    events = owner.get(f"/api/analysis-tasks/{task_id}/events")
    assert events.status_code == 200
    assert [event["to_status"] for event in events.json()["items"]] == ["PENDING", "QUEUED"]
    assert stranger.get(f"/api/analysis-tasks/{task_id}/events").status_code == 404
    assert stranger.post(f"/api/analysis-tasks/{task_id}/cancel").status_code == 404
    first = owner.post(f"/api/analysis-tasks/{task_id}/cancel")
    second = owner.post(f"/api/analysis-tasks/{task_id}/cancel")
    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "CANCELLED"
    assert len(broker.revocations) == 1
    assert owner.get(f"/api/analysis-tasks/{task_id}/events").json()["total"] == 3


def test_failed_retry_enqueues_once_and_clears_error(task_api):
    services, broker, owner, stranger, _ = task_api
    task_id = UUID(_created(owner).json()["task_id"])
    services.task_persistence.fail_task(task_id, code="EXECUTOR_FAILURE", message="internal detail")
    assert stranger.post(f"/api/analysis-tasks/{task_id}/retry").status_code == 404
    first = owner.post(f"/api/analysis-tasks/{task_id}/retry")
    second = owner.post(f"/api/analysis-tasks/{task_id}/retry")
    assert first.status_code == 202
    assert first.json()["status"] == "QUEUED"
    assert second.status_code == 409
    assert second.json()["code"] == "TASK_NOT_RETRYABLE"
    assert [message.task_id for message in broker.messages].count(task_id) == 2
    detail = owner.get(f"/api/analysis-tasks/{task_id}").json()
    assert detail["error"] is None
    assert owner.get(f"/api/analysis-tasks/{task_id}/events").json()["total"] == 4


def test_status_filter_and_pagination(task_api):
    _services, _broker, owner, _stranger, _ = task_api
    _created(owner, "one")
    _created(owner, "two")
    page = owner.get("/api/analysis-tasks", params={"status": "QUEUED", "page_size": 1})
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["has_next"] is True
    assert owner.get("/api/analysis-tasks", params={"page": 0}).status_code == 422
    assert owner.get("/api/analysis-tasks", params={"status": "UNKNOWN"}).status_code == 422


def test_retry_enqueue_failure_restores_failed_state(task_api):
    services, broker, owner, _stranger, _ = task_api
    task_id = UUID(_created(owner).json()["task_id"])
    services.task_persistence.fail_task(task_id, code="EXECUTOR_FAILURE", message="internal detail")
    broker.enqueue_error = RuntimeError("broker secret")

    response = owner.post(f"/api/analysis-tasks/{task_id}/retry")

    assert response.status_code == 503
    assert response.json()["code"] == "TASK_ENQUEUE_FAILED"
    detail = owner.get(f"/api/analysis-tasks/{task_id}")
    assert detail.json()["status"] == "FAILED"
    assert detail.json()["error"]["request_id"] == detail.headers["X-Request-ID"]
    assert "broker secret" not in str(detail.json())


def test_retry_claim_rejects_second_attempt(task_api):
    services, _broker, owner, _stranger, user_id = task_api
    task_id = UUID(_created(owner).json()["task_id"])
    services.task_persistence.fail_task(task_id, code="EXECUTOR_FAILURE", message="failed")

    services.task_persistence.retry_failed_task(task_id, user_id)

    from data_analysis_agent.services.persistence import TaskRetryConflictError
    with pytest.raises(TaskRetryConflictError):
        services.task_persistence.retry_failed_task(task_id, user_id)


def test_repository_rejects_stale_failed_snapshot(task_api):
    services, _broker, owner, _stranger, _user_id = task_api
    task_id = UUID(_created(owner).json()["task_id"])
    services.task_persistence.fail_task(task_id, code="FAILED", message="failed")
    queued = services.task_persistence.retry_failed_task(task_id, _user_id)

    with services.task_persistence.uow_factory() as uow:
        assert uow.tasks.update_if_status(queued, expected=TaskStatus.FAILED) is False
        assert uow.tasks.get(task_id).status is TaskStatus.QUEUED
