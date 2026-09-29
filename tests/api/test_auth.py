from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.persistence.orm_models import UserORM
from data_analysis_agent.persistence.unit_of_work import UnitOfWork


@pytest.fixture
def auth_api(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'auth.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
    )
    application = APIApplication.from_settings(settings)
    init_database(application.database.engine)
    app = create_app(application)
    try:
        yield SimpleNamespace(
            application=application,
            client=TestClient(app),
            unauthenticated=TestClient(app),
        )
    finally:
        application.database.engine.dispose()


def production_test_settings():
    return load_settings(
        app_env="production",
        environ={
            "APP_ENV": "production",
            "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://llm.example",
            "OPENAI_MODEL": "model",
            "DATABASE_URL": "mysql+pymysql://user:pass@db/app",
            "STORAGE_ENDPOINT": "https://s3.example",
            "STORAGE_BUCKET": "bucket",
            "EXECUTION_IMAGE": "analysis:latest",
        },
    )


def test_register_login_me_and_logout_invalidate_the_session(auth_api):
    client = auth_api.client
    registered = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["email"] == "owner@example.com"
    assert "password" not in registered.text

    login = client.post(
        "/api/auth/login",
        json={"email": "OWNER@example.com", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200, login.text
    assert "daa_session=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert "correct horse battery staple" not in login.text
    assert "token" not in login.json()

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_default_app_does_not_accept_a_user_id_header(auth_api):
    response = auth_api.unauthenticated.get(
        "/api/datasets", headers={"X-User-ID": str(uuid4())}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_duplicate_email_wrong_password_and_malformed_registration(auth_api):
    client = auth_api.client
    assert client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    ).status_code == 201
    duplicate = client.post(
        "/api/auth/register",
        json={"email": "OWNER@example.com", "password": "another correct password"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "EMAIL_ALREADY_REGISTERED"

    wrong_password = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "wrong password"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json()["code"] == "INVALID_CREDENTIALS"

    malformed = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "short"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "REQUEST_VALIDATION_ERROR"


def test_disabled_account_cannot_login_and_logout_without_cookie_is_safe(auth_api):
    client = auth_api.client
    registered = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    user_id = UUID(registered.json()["user_id"])
    with UnitOfWork(auth_api.application.database.session_factory) as uow:
        uow.session.execute(update(UserORM).where(UserORM.user_id == user_id).values(is_active=False))
        uow.commit()

    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    assert login.status_code == 401
    assert login.json()["code"] == "INVALID_CREDENTIALS"

    no_cookie_client = TestClient(create_app(auth_api.application))
    assert no_cookie_client.post("/api/auth/logout").status_code == 204


def test_production_rejects_header_principal_provider():
    settings = production_test_settings()
    application = APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())
    with pytest.raises(ConfigurationError, match="Header"):
        create_app(application)
