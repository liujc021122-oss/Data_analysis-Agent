import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.config.settings import load_settings


@pytest.fixture
def fake_application():
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test", "DATABASE_URL": "sqlite://"},
    )
    return APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())


def test_live_probe_does_not_require_dependencies(fake_application):
    response = TestClient(create_app(fake_application)).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_probe_returns_503_and_safe_statuses(monkeypatch, fake_application):
    monkeypatch.setattr(
        "data_analysis_agent.api.health.check_database", lambda database: False
    )
    monkeypatch.setattr(
        "data_analysis_agent.api.health.check_redis", lambda redis_url: True
    )
    monkeypatch.setattr(
        "data_analysis_agent.api.health.check_storage", lambda application: True
    )

    response = TestClient(create_app(fake_application)).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "components": {
            "database": "unavailable",
            "redis": "ok",
            "storage": "ok",
        },
    }


def test_ready_probe_is_ok_when_all_dependencies_are_healthy(
    monkeypatch, fake_application
):
    monkeypatch.setattr("data_analysis_agent.api.health.check_database", lambda database: True)
    monkeypatch.setattr("data_analysis_agent.api.health.check_redis", lambda redis_url: True)
    monkeypatch.setattr("data_analysis_agent.api.health.check_storage", lambda application: True)

    response = TestClient(create_app(fake_application)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "components": {"database": "ok", "redis": "ok", "storage": "ok"},
    }
