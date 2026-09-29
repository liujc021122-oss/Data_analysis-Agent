from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.persistence.database import init_database


@pytest.fixture
def authorization_api(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'authorization.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
            "AUTH_ADMIN_EMAILS": "admin@example.com",
        },
    )
    application = APIApplication.from_settings(settings)
    init_database(application.database.engine)
    app = create_app(application)
    try:
        yield application, TestClient(app), TestClient(app), TestClient(app)
    finally:
        application.database.engine.dispose()


def _register_and_login(client: TestClient, email: str) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert login.status_code == 200, login.text


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/datasets",
        files={"file": ("sales.csv", b"name,value\na,1\n", "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def _create_task(client: TestClient, *, key: str, dataset_id: str | None = None) -> str:
    payload = {"query": "analyze sales", "idempotency_key": key}
    if dataset_id is not None:
        payload["dataset_ids"] = [dataset_id]
    response = client.post("/api/analysis-tasks", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


def test_foreign_resources_are_hidden_but_admin_can_access_them(authorization_api):
    application, owner, foreign, admin = authorization_api
    _register_and_login(owner, "owner@example.com")
    _register_and_login(foreign, "foreign@example.com")
    _register_and_login(admin, "admin@example.com")

    dataset_id = _upload(owner)
    task_id = _create_task(owner, key="owner-task", dataset_id=dataset_id)

    foreign_dataset = foreign.get(f"/api/datasets/{dataset_id}")
    foreign_task = foreign.get(f"/api/analysis-tasks/{task_id}")
    foreign_events = foreign.get(f"/api/analysis-tasks/{task_id}/events")
    foreign_cancel = foreign.post(f"/api/analysis-tasks/{task_id}/cancel")
    foreign_retry = foreign.post(f"/api/analysis-tasks/{task_id}/retry")

    assert foreign_dataset.status_code == 404
    assert foreign_dataset.json()["code"] == "DATASET_NOT_FOUND"
    assert foreign_task.status_code == 404
    assert foreign_task.json()["code"] == "TASK_NOT_FOUND"
    assert foreign_events.status_code == 404
    assert foreign_events.json()["code"] == "TASK_NOT_FOUND"
    assert foreign_cancel.status_code == 404
    assert foreign_cancel.json()["code"] == "TASK_NOT_FOUND"
    assert foreign_retry.status_code == 404
    assert foreign_retry.json()["code"] == "TASK_NOT_FOUND"

    foreign_create = foreign.post(
        "/api/analysis-tasks",
        json={
            "query": "reuse foreign dataset",
            "idempotency_key": "foreign-dataset",
            "dataset_ids": [dataset_id],
        },
    )
    assert foreign_create.status_code == 404
    assert foreign_create.json()["code"] == "DATASET_NOT_FOUND"

    listed_datasets = admin.get("/api/datasets")
    assert listed_datasets.status_code == 200
    assert listed_datasets.json()["total"] == 1
    assert admin.get(f"/api/datasets/{dataset_id}").status_code == 200

    listed_tasks = admin.get("/api/analysis-tasks")
    assert listed_tasks.status_code == 200
    assert listed_tasks.json()["total"] == 1
    assert admin.get(f"/api/analysis-tasks/{task_id}").status_code == 200
    assert admin.get(f"/api/analysis-tasks/{task_id}/events").status_code == 200

    application.task_persistence.fail_task(
        UUID(task_id), code="EXECUTOR_FAILURE", message="worker failed"
    )
    admin_retry = admin.post(f"/api/analysis-tasks/{task_id}/retry")
    assert admin_retry.status_code == 202, admin_retry.text


def test_unknown_and_foreign_ids_use_the_same_not_found_contract(authorization_api):
    _application, owner, foreign, _admin = authorization_api
    _register_and_login(owner, "owner@example.com")
    _register_and_login(foreign, "foreign@example.com")
    dataset_id = _upload(owner)
    task_id = _create_task(owner, key="owner-task", dataset_id=dataset_id)

    checks = [
        (foreign.get(f"/api/datasets/{dataset_id}"), "DATASET_NOT_FOUND"),
        (foreign.get(f"/api/datasets/{uuid4()}"), "DATASET_NOT_FOUND"),
        (foreign.get(f"/api/analysis-tasks/{task_id}"), "TASK_NOT_FOUND"),
        (foreign.get(f"/api/analysis-tasks/{uuid4()}"), "TASK_NOT_FOUND"),
    ]
    for response, code in checks:
        assert response.status_code == 404
        assert response.json()["code"] == code
        assert set(response.json()) == {"code", "message", "details", "request_id"}
        assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_idempotency_keys_are_scoped_to_each_user(authorization_api):
    _application, owner, foreign, _admin = authorization_api
    _register_and_login(owner, "owner@example.com")
    _register_and_login(foreign, "foreign@example.com")

    owner_task = _create_task(owner, key="same-key")
    foreign_task = _create_task(foreign, key="same-key")

    assert owner_task != foreign_task
    assert owner.get(f"/api/analysis-tasks/{owner_task}").status_code == 200
    assert foreign.get(f"/api/analysis-tasks/{foreign_task}").status_code == 200
    assert foreign.get(f"/api/analysis-tasks/{owner_task}").status_code == 404

