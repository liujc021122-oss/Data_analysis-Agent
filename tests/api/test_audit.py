from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.domain.enums import AuditAction
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.persistence.orm_models import AuditEventORM
from data_analysis_agent.persistence.unit_of_work import UnitOfWork
from data_analysis_agent.services.audit import AuditWriter


@pytest.fixture
def audit_api(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'audit.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
            "AUTH_ADMIN_EMAILS": "admin@example.com",
        },
    )
    application = APIApplication.from_settings(settings)
    init_database(application.database.engine)
    app = create_app(application)
    try:
        yield application, TestClient(app), TestClient(app)
    finally:
        application.database.engine.dispose()


def _events(application):
    with UnitOfWork(application.database.session_factory) as uow:
        return list(
            uow.session.scalars(
                select(AuditEventORM).order_by(
                    AuditEventORM.occurred_at, AuditEventORM.event_id
                )
            )
        )


def _register_and_login(client, email, prefix):
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse battery staple"},
        headers={"X-Request-ID": f"{prefix}-register"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
        headers={"X-Request-ID": f"{prefix}-login"},
    )
    assert logged_in.status_code == 200, logged_in.text
    return UUID(registered.json()["user_id"])


def test_sensitive_api_operations_write_scoped_audit_events(audit_api):
    application, owner, foreign = audit_api
    owner_id = _register_and_login(owner, "owner@example.com", "owner")
    foreign_id = _register_and_login(foreign, "foreign@example.com", "foreign")

    uploaded = owner.post(
        "/api/datasets",
        files={"file": ("sales.csv", b"name,value\na,1\n", "text/csv")},
        headers={"X-Request-ID": "dataset-upload"},
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset_id = uploaded.json()["dataset_id"]
    deletable = owner.post(
        "/api/datasets",
        files={"file": ("delete.csv", b"name,value\nb,2\n", "text/csv")},
        headers={"X-Request-ID": "dataset-upload-delete"},
    )
    assert deletable.status_code == 201, deletable.text
    deletable_id = deletable.json()["dataset_id"]

    created_for_cancel = owner.post(
        "/api/analysis-tasks",
        json={"query": "cancel sales", "idempotency_key": "cancel-key"},
        headers={"X-Request-ID": "task-create-cancel"},
    )
    assert created_for_cancel.status_code == 202, created_for_cancel.text
    cancel_task_id = created_for_cancel.json()["task_id"]
    cancelled = owner.post(
        f"/api/analysis-tasks/{cancel_task_id}/cancel",
        headers={"X-Request-ID": "task-cancel"},
    )
    assert cancelled.status_code == 200, cancelled.text

    created_for_retry = owner.post(
        "/api/analysis-tasks",
        json={
            "query": "retry sales",
            "idempotency_key": "retry-key",
            "dataset_ids": [dataset_id],
        },
        headers={"X-Request-ID": "task-create-retry"},
    )
    assert created_for_retry.status_code == 202, created_for_retry.text
    retry_task_id = UUID(created_for_retry.json()["task_id"])
    application.task_persistence.fail_task(
        retry_task_id, code="EXECUTOR_FAILURE", message="worker failed"
    )
    retried = owner.post(
        f"/api/analysis-tasks/{retry_task_id}/retry",
        headers={"X-Request-ID": "task-retry"},
    )
    assert retried.status_code == 202, retried.text

    stored = application.storage.put(
        BytesIO(b"# report\n"),
        key=f"tasks/{retry_task_id}/reports/{uuid4()}_report.md",
        content_type="text/markdown",
    )
    with UnitOfWork(application.database.session_factory) as uow:
        task = application.task_persistence.get_task(retry_task_id)
        artifact = uow.artifacts.add(
            ArtifactRecord(
                task_id=retry_task_id,
                artifact_type="REPORT",
                name="report.md",
                file_path=stored.uri,
                format="MARKDOWN",
                mime_type="text/markdown",
                content_hash=stored.checksum,
                size_bytes=stored.size_bytes,
                created_at=task.created_at,
            )
        )
        uow.commit()

    download = owner.get(
        f"/api/artifacts/{artifact.artifact_id}/download",
        headers={"X-Request-ID": "artifact-download-metadata"},
    )
    assert download.status_code == 200, download.text
    content = owner.get(
        download.json()["content_url"],
        headers={"X-Request-ID": "artifact-download"},
    )
    assert content.status_code == 200, content.text
    assert content.content == b"# report\n"

    denied = foreign.get(
        f"/api/datasets/{dataset_id}", headers={"X-Request-ID": "foreign-denied"}
    )
    assert denied.status_code == 404, denied.text

    deleted = owner.delete(
        f"/api/datasets/{deletable_id}", headers={"X-Request-ID": "dataset-delete"}
    )
    assert deleted.status_code == 204, deleted.text

    logged_out = owner.post(
        "/api/auth/logout", headers={"X-Request-ID": "owner-logout"}
    )
    assert logged_out.status_code == 204, logged_out.text

    events = _events(application)
    by_action = {action: [event for event in events if event.action.value == action] for action in AuditAction}

    expected = {
        AuditAction.REGISTERED: ("owner-register", owner_id, True),
        AuditAction.LOGIN_SUCCEEDED: ("owner-login", owner_id, True),
        AuditAction.LOGGED_OUT: ("owner-logout", owner_id, True),
        AuditAction.DATASET_UPLOADED: ("dataset-upload", owner_id, True),
        AuditAction.DATASET_DELETED: ("dataset-delete", owner_id, True),
        AuditAction.TASK_CREATED: ("task-create-cancel", owner_id, True),
        AuditAction.TASK_CANCELLED: ("task-cancel", owner_id, True),
        AuditAction.TASK_RETRIED: ("task-retry", owner_id, True),
        AuditAction.ARTIFACT_DOWNLOAD_SUCCEEDED: ("artifact-download", owner_id, True),
        AuditAction.AUTHORIZATION_DENIED: ("foreign-denied", foreign_id, False),
    }
    for action, (request_id, user_id, success) in expected.items():
        matching = [
            event
            for event in by_action[action]
            if event.request_id == request_id
        ]
        assert len(matching) == 1, (action, matching)
        assert matching[0].user_id == user_id
        assert matching[0].success is success

    serialized = repr([event.metadata_json for event in events])
    for secret in (
        "password",
        "password_hash",
        "daa_session",
        "token",
        "file_path",
        "source_uri",
        "storage_uri",
        "DATABASE_URL",
    ):
        assert secret not in serialized


def test_audit_writer_keeps_only_safe_scalar_metadata(audit_api):
    application, _owner, _foreign = audit_api
    writer = AuditWriter(
        lambda: UnitOfWork(application.database.session_factory)
    )
    with UnitOfWork(application.database.session_factory) as uow:
        event = writer.record_in_uow(
            uow,
            action=AuditAction.AUTHORIZATION_DENIED,
            user_id=None,
            request_id="request-1",
            target_type="dataset",
            target_id=uuid4(),
            success=False,
            metadata={
                "role": "USER",
                "status_code": 404,
                "password": "secret",
                "file_path": "C:/private/data.csv",
            },
        )
        uow.commit()

    assert event.metadata_json == {"role": "USER", "status_code": 404}


def test_unauthenticated_business_access_is_audited(audit_api):
    application, _owner, unauthenticated = audit_api

    response = unauthenticated.get(
        "/api/datasets", headers={"X-Request-ID": "auth-denied"}
    )

    assert response.status_code == 401
    events = [event for event in _events(application) if event.request_id == "auth-denied"]
    assert len(events) == 1
    assert events[0].action is AuditAction.AUTHENTICATION_DENIED
    assert events[0].user_id is None
    assert events[0].metadata_json == {"reason_code": "AUTHENTICATION_REQUIRED"}


def test_admin_cross_user_reads_are_audited(audit_api):
    application, owner, admin = audit_api
    _register_and_login(owner, "owner@example.com", "owner-read")
    _register_and_login(admin, "admin@example.com", "admin-read")

    uploaded = owner.post(
        "/api/datasets",
        files={"file": ("sales.csv", b"name,value\na,1\n", "text/csv")},
        headers={"X-Request-ID": "owner-read-upload"},
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset_id = uploaded.json()["dataset_id"]

    created = owner.post(
        "/api/analysis-tasks",
        json={"query": "admin read", "idempotency_key": "admin-read-task"},
        headers={"X-Request-ID": "owner-read-task"},
    )
    assert created.status_code == 202, created.text
    task_id = UUID(created.json()["task_id"])

    stored = application.storage.put(
        BytesIO(b"# report\n"),
        key=f"tasks/{task_id}/reports/{uuid4()}_report.md",
        content_type="text/markdown",
    )
    with UnitOfWork(application.database.session_factory) as uow:
        task = application.task_persistence.get_task(task_id)
        artifact = uow.artifacts.add(
            ArtifactRecord(
                task_id=task_id,
                artifact_type="REPORT",
                name="report.md",
                file_path=stored.uri,
                format="MARKDOWN",
                mime_type="text/markdown",
                content_hash=stored.checksum,
                size_bytes=stored.size_bytes,
                created_at=task.created_at,
            )
        )
        uow.commit()

    requests = {
        "admin-dataset-list": admin.get(
            "/api/datasets", headers={"X-Request-ID": "admin-dataset-list"}
        ),
        "admin-dataset-get": admin.get(
            f"/api/datasets/{dataset_id}",
            headers={"X-Request-ID": "admin-dataset-get"},
        ),
        "admin-task-list": admin.get(
            "/api/analysis-tasks", headers={"X-Request-ID": "admin-task-list"}
        ),
        "admin-task-get": admin.get(
            f"/api/analysis-tasks/{task_id}",
            headers={"X-Request-ID": "admin-task-get"},
        ),
        "admin-task-events": admin.get(
            f"/api/analysis-tasks/{task_id}/events",
            headers={"X-Request-ID": "admin-task-events"},
        ),
        "admin-artifact-get": admin.get(
            f"/api/artifacts/{artifact.artifact_id}",
            headers={"X-Request-ID": "admin-artifact-get"},
        ),
        "admin-artifact-download": admin.get(
            f"/api/artifacts/{artifact.artifact_id}/download",
            headers={"X-Request-ID": "admin-artifact-download"},
        ),
    }
    assert all(response.status_code == 200 for response in requests.values()), {
        request_id: response.text for request_id, response in requests.items()
    }

    events = _events(application)
    for request_id in requests:
        matching = [
            event
            for event in events
            if event.request_id == request_id
        ]
        assert len(matching) == 1, (request_id, matching)
        assert matching[0].action.value == "ADMIN_CROSS_USER_ACCESS"
        assert matching[0].success is True
        assert matching[0].metadata_json == {
            "resource_type": matching[0].target_type,
            "role": "ADMIN",
        }
