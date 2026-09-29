from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select, update

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.domain.enums import AuditAction, UserRole
from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.orm_models import AuditEventORM, UserORM
from data_analysis_agent.persistence.unit_of_work import UnitOfWork
from data_analysis_agent.services.auth import (
    AuthenticationService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)


@pytest.fixture
def uow_factory(tmp_path: Path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'auth-service.sqlite3'}",
        },
    )
    database = Database.from_settings(settings)
    init_database(database.engine)
    try:
        yield lambda: UnitOfWork(database.session_factory)
    finally:
        database.engine.dispose()


@pytest.fixture
def auth_service(uow_factory):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "AUTH_SESSION_TTL_SECONDS": "300",
            "AUTH_ADMIN_EMAILS": "Admin@Example.com",
        },
    )
    return AuthenticationService(uow_factory, settings)


def _audit_events(uow_factory):
    with uow_factory() as uow:
        rows = uow.session.scalars(
            select(AuditEventORM).order_by(AuditEventORM.occurred_at, AuditEventORM.event_id)
        ).all()
        return [
            {
                "action": row.action.value if hasattr(row.action, "value") else row.action,
                "user_id": row.user_id,
                "success": row.success,
                "request_id": row.request_id,
                "metadata": row.metadata_json,
            }
            for row in rows
        ]


def test_register_normalizes_email_and_assigns_configured_admin_role(auth_service):
    user = auth_service.register(
        email="  Admin@Example.com ",
        password="correct horse battery staple",
        request_id="req-register",
    )

    assert user.email == "admin@example.com"
    assert user.role is UserRole.ADMIN
    assert user.is_active is True


def test_duplicate_normalized_email_raises_stable_error(auth_service):
    auth_service.register(
        email="Owner@Example.com",
        password="correct horse battery staple",
        request_id="req-first",
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        auth_service.register(
            email=" owner@example.com ",
            password="another correct password",
            request_id="req-duplicate",
        )


def test_login_returns_raw_token_while_only_hash_is_persisted(auth_service, uow_factory):
    registered = auth_service.register(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-register",
    )

    result = auth_service.login(
        email="OWNER@example.com",
        password="correct horse battery staple",
        request_id="req-login",
    )

    assert result.user == registered
    assert result.token
    assert result.token != hashlib.sha256(result.token.encode("ascii")).hexdigest()
    with uow_factory() as uow:
        session = uow.sessions.get_active_by_token_hash(
            hashlib.sha256(result.token.encode("ascii")).hexdigest(),
            datetime.now(timezone.utc),
        )
    assert session is not None
    assert session.token_hash == hashlib.sha256(result.token.encode("ascii")).hexdigest()
    assert result.token not in json.dumps(session.model_dump(), default=str)


def test_wrong_password_and_disabled_user_are_invalid_credentials(auth_service, uow_factory):
    user = auth_service.register(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-register",
    )

    with pytest.raises(InvalidCredentialsError):
        auth_service.login(
            email="owner@example.com",
            password="wrong password",
            request_id="req-wrong-password",
        )

    with uow_factory() as uow:
        uow.session.execute(
            update(UserORM).where(UserORM.user_id == user.user_id).values(is_active=False)
        )
        uow.commit()

    with pytest.raises(InvalidCredentialsError):
        auth_service.login(
            email="owner@example.com",
            password="correct horse battery staple",
            request_id="req-disabled",
        )


def test_active_expired_and_revoked_sessions(auth_service):
    registered = auth_service.register(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-register",
    )
    result = auth_service.login(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-login",
    )

    assert auth_service.authenticate_session(token=result.token) == registered
    assert auth_service.authenticate_session(
        token=result.token,
        now=result.expires_at + timedelta(microseconds=1),
    ) is None

    second = auth_service.login(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-login-2",
    )
    assert auth_service.logout(token=second.token, request_id="req-logout") is True
    assert auth_service.authenticate_session(token=second.token) is None


def test_logout_is_idempotent(auth_service):
    auth_service.register(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-register",
    )
    result = auth_service.login(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-login",
    )

    assert auth_service.logout(token=result.token, request_id="req-logout") is True
    assert auth_service.logout(token=result.token, request_id="req-logout-again") is False
    assert auth_service.logout(token="unknown", request_id="req-unknown") is False


def test_authentication_actions_write_allowlisted_audit_events(auth_service, uow_factory):
    user = auth_service.register(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-register",
    )
    login = auth_service.login(
        email="owner@example.com",
        password="correct horse battery staple",
        request_id="req-login",
    )
    with pytest.raises(InvalidCredentialsError):
        auth_service.login(
            email="owner@example.com",
            password="wrong password",
            request_id="req-failed-login",
        )
    auth_service.logout(token=login.token, request_id="req-logout")

    events = _audit_events(uow_factory)
    assert [event["action"] for event in events] == [
        AuditAction.REGISTERED.value,
        AuditAction.LOGIN_SUCCEEDED.value,
        AuditAction.LOGIN_FAILED.value,
        AuditAction.LOGGED_OUT.value,
    ]
    assert events[0]["user_id"] == user.user_id
    assert events[1]["user_id"] == user.user_id
    assert events[2]["user_id"] == user.user_id
    assert events[3]["user_id"] == user.user_id
    assert [event["request_id"] for event in events] == [
        "req-register",
        "req-login",
        "req-failed-login",
        "req-logout",
    ]
    allowed_metadata = {"email_domain", "role"}
    forbidden = {"password", "password_hash", "token", "cookie", "correct horse battery staple"}
    for event in events:
        assert set(event["metadata"]) <= allowed_metadata
        serialized = json.dumps(event["metadata"]).lower()
        assert not forbidden & set(serialized.split())
        assert all(secret not in serialized for secret in forbidden)
