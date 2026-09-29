from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.datasets.errors import (
    DatasetErrorCode,
    DatasetPersistenceError,
    StorageError as DatasetStorageError,
)
from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.errors import TransactionError
from data_analysis_agent.persistence.errors import PersistenceError
from data_analysis_agent.persistence.repositories import DatasetRepository
from data_analysis_agent.persistence.unit_of_work import UnitOfWork
from data_analysis_agent.storage.errors import StorageError, StorageErrorCode


@pytest.fixture
def dataset_api(tmp_path):
    settings = load_settings(app_env="test", environ={
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'api.sqlite3'}",
        "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
    })
    database = Database.from_settings(settings)
    init_database(database.engine)
    application = APIApplication.from_settings(settings)
    application.principal_provider = HeaderPrincipalProvider()
    owner_id, other_id = uuid4(), uuid4()

    def client(user_id):
        return TestClient(create_app(application), headers={"X-User-ID": str(user_id)})

    try:
        yield application, client(owner_id), client(other_id), owner_id, other_id
    finally:
        database.engine.dispose()
        application.database.engine.dispose()


def _upload(client, name="sample.csv", content=b"name,value\na,1\n"):
    return client.post("/api/datasets", files={"file": (name, content, "text/csv")})


def _assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_upload_and_detail_hide_storage_paths(dataset_api):
    _app, owner, _other, *_ = dataset_api
    uploaded = _upload(owner)
    assert uploaded.status_code == 201
    dataset_id = uploaded.json()["dataset_id"]
    assert uploaded.json()["profile"]["row_count"] == 1
    detail = owner.get(f"/api/datasets/{dataset_id}")
    assert detail.status_code == 200
    assert detail.json()["dataset_id"] == dataset_id
    assert detail.json()["profile"]["preview_rows"][0]["value"] == "1"
    for response in (uploaded, detail):
        assert response.headers["X-Request-ID"]
        assert not any(key in response.text for key in ("source_uri", "file_path", "storage_uri"))


def test_pagination_and_owner_isolation(dataset_api):
    _app, owner, other, *_ = dataset_api
    first = _upload(owner).json()["dataset_id"]
    second = _upload(owner, "second.csv").json()["dataset_id"]
    page = owner.get("/api/datasets", params={"page": 1, "page_size": 1})
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["has_next"] is True
    assert page.json()["items"][0]["dataset_id"] == first
    assert owner.get("/api/datasets", params={"page": 2, "page_size": 1}).json()["items"][0]["dataset_id"] == second
    assert other.get("/api/datasets").json()["total"] == 0
    assert "source_uri" not in page.text
    assert "file_path" not in page.text
    assert "storage_uri" not in page.text
    _assert_error(other.get(f"/api/datasets/{first}"), 404, "DATASET_NOT_FOUND")
    _assert_error(other.delete(f"/api/datasets/{first}"), 404, "DATASET_NOT_FOUND")


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}])
def test_invalid_pagination_returns_uniform_validation_error(dataset_api, params):
    _app, owner, *_ = dataset_api
    _assert_error(owner.get("/api/datasets", params=params), 422, "REQUEST_VALIDATION_ERROR")


def test_delete_removes_object_and_metadata(dataset_api):
    app, owner, _other, owner_id, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]
    with UnitOfWork(app.database.session_factory) as uow:
        uri = uow.datasets.get_for_user(dataset_id, owner_id).source_uri
    response = owner.delete(f"/api/datasets/{dataset_id}")
    assert response.status_code == 204
    assert not app.storage.exists(uri)
    _assert_error(owner.get(f"/api/datasets/{dataset_id}"), 404, "DATASET_NOT_FOUND")


def test_invalid_and_oversized_uploads_use_uniform_errors(dataset_api):
    app, owner, *_ = dataset_api
    _assert_error(_upload(owner, content=b"a,a\n1,2\n"), 422, "DUPLICATE_COLUMNS")
    app.dataset_upload._max_upload_size = 8
    _assert_error(_upload(owner), 413, "FILE_TOO_LARGE")


def test_storage_and_persistence_upload_failures_are_unavailable(dataset_api, monkeypatch):
    app, owner, *_ = dataset_api
    def storage_failure(*args, **kwargs):
        raise DatasetStorageError(DatasetErrorCode.STORAGE_FAILURE, "secret storage path")
    monkeypatch.setattr(app.storage, "put", storage_failure)
    response = _upload(owner)
    _assert_error(response, 503, "STORAGE_FAILURE")
    assert "secret storage path" not in response.text
    monkeypatch.undo()
    def persistence_failure(*args, **kwargs):
        raise DatasetPersistenceError(DatasetErrorCode.DATASET_PERSISTENCE_FAILURE, "secret database")
    monkeypatch.setattr(app.dataset_upload._metadata_store, "create", persistence_failure)
    response = _upload(owner)
    _assert_error(response, 503, "DATASET_PERSISTENCE_FAILURE")
    assert "secret database" not in response.text


def test_missing_catalog_service_returns_503_for_all_reads_and_delete(dataset_api):
    app, owner, *_ = dataset_api
    app.dataset_catalog = None
    _assert_error(owner.get("/api/datasets"), 503, "DATASET_SERVICE_UNAVAILABLE")
    _assert_error(owner.get(f"/api/datasets/{uuid4()}"), 503, "DATASET_SERVICE_UNAVAILABLE")
    _assert_error(owner.delete(f"/api/datasets/{uuid4()}"), 503, "DATASET_SERVICE_UNAVAILABLE")


def test_storage_delete_failure_keeps_metadata(dataset_api, monkeypatch):
    app, owner, _other, owner_id, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]
    def failure(uri):
        raise DatasetStorageError(DatasetErrorCode.STORAGE_FAILURE, "secret storage path")
    monkeypatch.setattr(app.storage, "delete", failure)
    _assert_error(owner.delete(f"/api/datasets/{dataset_id}"), 503, "STORAGE_FAILURE")
    with UnitOfWork(app.database.session_factory) as uow:
        assert uow.datasets.get_for_user(dataset_id, owner_id) is not None


def test_canonical_storage_delete_failure_uses_stable_error(dataset_api, monkeypatch):
    app, owner, _other, owner_id, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]

    def failure(uri):
        raise StorageError(StorageErrorCode.BACKEND_UNAVAILABLE, "secret storage path")

    monkeypatch.setattr(app.storage, "delete", failure)
    response = owner.delete(f"/api/datasets/{dataset_id}")
    _assert_error(response, 503, "STORAGE_FAILURE")
    assert "secret storage path" not in response.text
    with UnitOfWork(app.database.session_factory) as uow:
        assert uow.datasets.get_for_user(dataset_id, owner_id) is not None


@pytest.mark.parametrize("operation", ["list", "get", "delete"])
def test_catalog_database_failure_uses_stable_error(dataset_api, monkeypatch, operation):
    app, owner, _other, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]

    def failure(*args, **kwargs):
        raise PersistenceError("secret SQL statement")

    method = {"list": "list_for_user", "get": "get_for_user", "delete": "delete_for_user"}[operation]
    monkeypatch.setattr(DatasetRepository, method, failure)
    response = (
        owner.get("/api/datasets") if operation == "list" else
        owner.get(f"/api/datasets/{dataset_id}") if operation == "get" else
        owner.delete(f"/api/datasets/{dataset_id}")
    )
    _assert_error(response, 503, "DATASET_PERSISTENCE_FAILURE")
    assert "secret SQL statement" not in response.text


def test_metadata_delete_failure_returns_stable_error(dataset_api, monkeypatch):
    app, owner, _other, owner_id, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]
    with UnitOfWork(app.database.session_factory) as uow:
        uri = uow.datasets.get_for_user(dataset_id, owner_id).source_uri
    def failure(self, dataset_id, user_id):
        raise TransactionError("secret SQL statement")
    monkeypatch.setattr(DatasetRepository, "delete_for_user", failure)
    response = owner.delete(f"/api/datasets/{dataset_id}")
    _assert_error(response, 503, "DATASET_PERSISTENCE_FAILURE")
    assert "secret SQL statement" not in response.text
    assert not app.storage.exists(uri)
    with UnitOfWork(app.database.session_factory) as uow:
        assert uow.datasets.get_for_user(dataset_id, owner_id) is not None


def test_malformed_profile_has_stable_error(dataset_api):
    app, owner, _other, owner_id, *_ = dataset_api
    dataset_id = _upload(owner).json()["dataset_id"]
    with UnitOfWork(app.database.session_factory) as uow:
        row = uow.datasets.get_for_user(dataset_id, owner_id)
        from data_analysis_agent.persistence.orm_models import DatasetORM
        uow.session.get(DatasetORM, dataset_id).metadata_json = {"profile": {"source_uri": "secret"}}
        uow.commit()
    response = owner.get(f"/api/datasets/{dataset_id}")
    _assert_error(response, 503, "DATASET_PROFILE_INVALID")
    assert "secret" not in response.text
