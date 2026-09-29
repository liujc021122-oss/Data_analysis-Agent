from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.api.auth import Principal
from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.domain.enums import UserRole
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.persistence.models import ArtifactRecord


@pytest.fixture
def artifact_api(tmp_path):
    settings = load_settings(app_env="test", environ={
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'artifacts.sqlite3'}",
        "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        "STORAGE_URL_EXPIRY": "60",
    })
    application = APIApplication.from_settings(settings)
    application.principal_provider = HeaderPrincipalProvider()
    init_database(application.database.engine)
    owner_id, foreign_id = uuid4(), uuid4()
    task = application.task_persistence.create_task(
        user_id=owner_id,
        request=AnalysisTaskCreateRequest(query="分析销售", idempotency_key="artifact-test"),
    )
    stored = application.storage.put(
        BytesIO(b"chart"),
        key=f"tasks/{task.task_id}/charts/{uuid4()}_chart.png",
        content_type="image/png",
    )
    with application.task_persistence.uow_factory() as uow:
        artifact = uow.artifacts.add(ArtifactRecord(
            task_id=task.task_id,
            artifact_type="CHART",
            name="chart.png",
            file_path=stored.uri,
            mime_type="image/png",
            size_bytes=stored.size_bytes,
            content_hash=stored.checksum,
            created_at=task.created_at,
            metadata_json={
                "source_uri": "C:/private/source.csv",
                "nested": {"file_path": "C:/private/chart.png", "series": "sales"},
            },
        ))
        uow.commit()
    app = create_app(application)
    try:
        yield (
            application,
            artifact,
            TestClient(app, headers={"X-User-ID": str(owner_id)}),
            TestClient(app, headers={"X-User-ID": str(foreign_id)}),
        )
    finally:
        application.database.engine.dispose()


def test_artifact_metadata_and_download_url_are_owner_scoped(artifact_api):
    application, artifact, owner, _foreign = artifact_api

    metadata = owner.get(f"/api/artifacts/{artifact.artifact_id}")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["artifact_id"] == str(artifact.artifact_id)
    assert metadata.json()["download_url"].startswith("local-download://")
    assert metadata.json()["content_url"].startswith(
        f"/api/artifacts/{artifact.artifact_id}/content?"
    )
    assert metadata.json()["metadata"]["nested"]["series"] == "sales"
    assert all(key not in str(metadata.json()) for key in ("file_path", "source_uri", "storage_uri", "C:/private"))
    assert "file_path" not in metadata.json()

    download = owner.get(f"/api/artifacts/{artifact.artifact_id}/download")
    assert download.status_code == 200, download.text
    assert download.json()["artifact_id"] == str(artifact.artifact_id)
    assert download.json()["download_url"].startswith("local-download://")
    assert download.json()["expires_in"] == application.settings.storage_url_expiry
    assert download.json()["content_url"] == metadata.json()["content_url"]

    content = owner.get(metadata.json()["content_url"])
    assert content.status_code == 200, content.text
    assert content.content == b"chart"
    assert content.headers["content-type"].startswith("image/png")

    foreign_content = _foreign.get(metadata.json()["content_url"])
    assert foreign_content.status_code == 404
    assert foreign_content.json()["code"] == "ARTIFACT_NOT_FOUND"


def test_foreign_and_unknown_artifact_use_same_not_found_contract(artifact_api):
    _application, artifact, owner, foreign = artifact_api
    for client, artifact_id in ((foreign, artifact.artifact_id), (owner, uuid4())):
        for suffix in ("", "/download"):
            response = client.get(f"/api/artifacts/{artifact_id}{suffix}")
            assert response.status_code == 404
            assert response.json()["code"] == "ARTIFACT_NOT_FOUND"
            assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_content_endpoint_rejects_missing_or_tampered_download_token(artifact_api):
    _application, artifact, owner, _foreign = artifact_api
    metadata = owner.get(f"/api/artifacts/{artifact.artifact_id}").json()
    signed_url = metadata["download_url"]
    tampered_url = signed_url[:-1] + ("A" if signed_url[-1] != "A" else "B")

    missing = owner.get(f"/api/artifacts/{artifact.artifact_id}/content")
    tampered = owner.get(
        f"/api/artifacts/{artifact.artifact_id}/content",
        params={"download_url": tampered_url},
    )

    assert missing.status_code == tampered.status_code == 404
    assert missing.json()["code"] == tampered.json()["code"] == "ARTIFACT_NOT_FOUND"


def test_missing_stored_artifact_returns_not_found_without_path(artifact_api):
    application, artifact, owner, _foreign = artifact_api
    application.storage.delete(artifact.file_path)

    for suffix in ("", "/download"):
        response = owner.get(f"/api/artifacts/{artifact.artifact_id}{suffix}")
        assert response.status_code == 404
        assert response.json()["code"] == "ARTIFACT_NOT_FOUND"
        assert artifact.file_path not in response.text


def test_changed_stored_artifact_returns_not_found(artifact_api):
    application, artifact, owner, _foreign = artifact_api
    application.storage.put(
        BytesIO(b"changed"),
        key=artifact.file_path.removeprefix("local://"),
        content_type="image/png",
    )

    response = owner.get(f"/api/artifacts/{artifact.artifact_id}/download")

    assert response.status_code == 404
    assert response.json()["code"] == "ARTIFACT_NOT_FOUND"


def test_artifact_routes_require_authentication(artifact_api):
    application, artifact, _owner, _foreign = artifact_api
    client = TestClient(create_app(application))

    response = client.get(f"/api/artifacts/{artifact.artifact_id}")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_admin_can_download_another_users_artifact(artifact_api):
    application, artifact, _owner, _foreign = artifact_api
    admin_user_id = uuid4()

    class AdminPrincipalProvider:
        def current_principal(self, request):
            return Principal(user_id=admin_user_id, role=UserRole.ADMIN)

    application.principal_provider = AdminPrincipalProvider()
    admin = TestClient(create_app(application))

    metadata = admin.get(f"/api/artifacts/{artifact.artifact_id}")
    assert metadata.status_code == 200, metadata.text
    content = admin.get(metadata.json()["content_url"])
    assert content.status_code == 200, content.text
    assert content.content == b"chart"
