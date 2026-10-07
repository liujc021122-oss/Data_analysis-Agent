from dataclasses import replace
import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.api.auth import PrincipalProvider
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.worker.broker import InMemoryTaskBroker


@pytest.fixture
def fake_application():
    settings = load_settings(app_env="test", environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"})
    return APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())


def test_request_id_is_generated_and_openapi_is_available(fake_application):
    response = TestClient(create_app(fake_application)).get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert "/api/datasets" in response.json()["paths"]


def test_create_app_configures_application_logging(monkeypatch, fake_application):
    configured = []
    monkeypatch.setattr(
        "data_analysis_agent.api.app.configure_logging",
        lambda settings: configured.append(settings),
        raising=False,
    )

    create_app(fake_application)

    assert configured == [fake_application.settings]


def test_openapi_lists_the_complete_m13_api_surface(fake_application):
    paths = TestClient(create_app(fake_application)).get("/openapi.json").json()["paths"]

    assert {
        "/api/datasets",
        "/api/datasets/{dataset_id}",
        "/api/analysis-tasks",
        "/api/analysis-tasks/{task_id}",
        "/api/analysis-tasks/{task_id}/cancel",
        "/api/analysis-tasks/{task_id}/retry",
        "/api/analysis-tasks/{task_id}/events",
        "/api/artifacts/{artifact_id}",
        "/api/artifacts/{artifact_id}/download",
        "/api/artifacts/{artifact_id}/content",
    } <= paths.keys()


def test_missing_identity_uses_uniform_error_shape(fake_application):
    response = TestClient(create_app(fake_application)).get("/api/datasets")

    assert response.status_code == 401
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_supplied_request_id_is_echoed(fake_application):
    response = TestClient(create_app(fake_application)).get("/openapi.json", headers={"X-Request-ID": "request-123"})
    assert response.headers["X-Request-ID"] == "request-123"


def test_invalid_request_id_is_replaced_and_public_factory_is_exported(fake_application):
    from data_analysis_agent.api import create_app as exported_create_app

    response = TestClient(exported_create_app(fake_application)).get(
        "/openapi.json", headers={"X-Request-ID": "x" * 129}
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "x" * 129
    assert len(response.headers["X-Request-ID"]) == 36


def test_default_test_application_does_not_install_header_principal_provider():
    application = APIApplication(settings=load_settings(
        app_env="test", environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"}
    ))
    create_app(application)
    assert application.principal_provider is None


def test_development_uses_celery_broker_when_redis_is_configured(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'development.sqlite3'}",
            "REDIS_URL": "redis://localhost:6379/0",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
    )
    broker = object()
    monkeypatch.setattr(
        "data_analysis_agent.worker.celery_app.build_celery_broker",
        lambda configured: broker,
    )

    application = APIApplication.from_settings(settings)

    assert application.task_submission.broker is broker
    assert not isinstance(application.task_submission.broker, InMemoryTaskBroker)


def test_development_without_redis_rejects_async_submission_with_configuration_error(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'development.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
        dotenv_dir=tmp_path,
    )
    application = APIApplication.from_settings(settings)
    init_database(application.database.engine)
    application.principal_provider = HeaderPrincipalProvider()
    try:
        assert application.task_submission is None
        response = TestClient(
            create_app(application),
            headers={"X-User-ID": "00000000-0000-0000-0000-000000000001"},
        ).post(
            "/api/analysis-tasks",
            json={"query": "分析销售", "idempotency_key": "development-key"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "TASK_BROKER_NOT_CONFIGURED"
    finally:
        application.database.engine.dispose()


def test_test_application_keeps_in_memory_task_broker(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'test.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
    )
    application = APIApplication.from_settings(settings)

    assert isinstance(application.task_submission.broker, InMemoryTaskBroker)


def test_production_requires_explicit_principal_provider(fake_application):
    production = replace(fake_application, settings=load_settings(
        app_env="production",
        environ={
            "APP_ENV": "production", "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://api.example", "OPENAI_MODEL": "model",
            "DATABASE_URL": "mysql+pymysql://u:p@localhost/db",
            "STORAGE_ENDPOINT": "http://storage", "STORAGE_BUCKET": "bucket",
            "STORAGE_SIGNING_SECRET": "signing-secret",
        },
    ), principal_provider=None)
    with pytest.raises(ConfigurationError, match="authentication provider"):
        create_app(production)


def test_production_rejects_header_principal_provider(fake_application):
    production = replace(fake_application, settings=load_settings(
        app_env="production",
        environ={
            "APP_ENV": "production", "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://api.example", "OPENAI_MODEL": "model",
            "DATABASE_URL": "mysql+pymysql://u:p@localhost/db",
            "STORAGE_ENDPOINT": "http://storage", "STORAGE_BUCKET": "bucket",
            "STORAGE_SIGNING_SECRET": "signing-secret",
        },
    ), principal_provider=HeaderPrincipalProvider())
    with pytest.raises(ConfigurationError, match="non-header"):
        create_app(production)


def test_validation_errors_do_not_echo_submitted_values(fake_application):
    client = TestClient(create_app(fake_application))
    response = client.post(
        "/api/datasets",
        data={"sensitive": "SECRET_VALUE"},
        headers={"X-Request-ID": "validation-test", "X-User-ID": "00000000-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["details"]["errors"][0]["msg"] == "invalid request"
    assert "SECRET_VALUE" not in response.text


def test_api_application_service_annotations_are_explicit():
    annotations = APIApplication.__annotations__
    assert annotations["database"] != "Any"
    for name in ("dataset_upload", "dataset_catalog", "task_persistence", "task_submission", "file_access"):
        assert annotations[name] != "Any"
