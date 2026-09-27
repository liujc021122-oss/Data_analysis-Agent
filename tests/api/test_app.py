from dataclasses import replace
import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.config.settings import ConfigurationError, load_settings


@pytest.fixture
def fake_application():
    settings = load_settings(app_env="test", environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"})
    return APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())


def test_request_id_is_generated_and_openapi_is_available(fake_application):
    response = TestClient(create_app(fake_application)).get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert "/api/datasets" not in response.json()["paths"]


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
