from dataclasses import replace
import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.api.auth import PrincipalProvider
from data_analysis_agent.config.settings import ConfigurationError, load_settings


@pytest.fixture
def fake_application():
    settings = load_settings(app_env="test", environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"})
    return APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())


def test_request_id_is_generated_and_openapi_is_available(fake_application):
    response = TestClient(create_app(fake_application)).get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert "/api/datasets" in response.json()["paths"]


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


def test_default_test_application_uses_header_principal_provider():
    application = APIApplication(settings=load_settings(
        app_env="test", environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"}
    ))
    create_app(application)
    assert isinstance(application.principal_provider, HeaderPrincipalProvider)


def test_production_requires_explicit_principal_provider(fake_application):
    production = replace(fake_application, settings=load_settings(
        app_env="production",
        environ={
            "APP_ENV": "production", "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://api.example", "OPENAI_MODEL": "model",
            "DATABASE_URL": "mysql+pymysql://u:p@localhost/db",
            "STORAGE_ENDPOINT": "http://storage", "STORAGE_BUCKET": "bucket",
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
