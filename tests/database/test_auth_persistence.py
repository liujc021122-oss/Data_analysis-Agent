from datetime import datetime, timedelta, timezone
from uuid import uuid4

from data_analysis_agent.domain.enums import AuditAction, UserRole
from data_analysis_agent.persistence.models import (
    AuditEventRecord,
    AuthSessionRecord,
    UserRecord,
)


def test_user_repository_round_trips_credentials_role_and_active_state(uow_factory):
    now = datetime.now(timezone.utc)
    record = UserRecord(
        email_normalized="owner@example.com",
        password_hash="argon2id$test",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
    )

    with uow_factory() as uow:
        uow.users.ensure(record)
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.users.get_by_email("owner@example.com")

    assert loaded is not None
    assert loaded.user_id == record.user_id
    assert loaded.password_hash == "argon2id$test"
    assert loaded.role is UserRole.ADMIN
    assert loaded.is_active is True


def test_ensure_legacy_user_does_not_overwrite_authentication_fields(uow_factory):
    now = datetime.now(timezone.utc)
    existing = UserRecord(
        email_normalized="owner@example.com",
        password_hash="argon2id$test",
        role=UserRole.ADMIN,
        created_at=now,
    )

    with uow_factory() as uow:
        uow.users.ensure(existing)
        uow.users.ensure(UserRecord(user_id=existing.user_id, created_at=now))
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.users.get(existing.user_id)

    assert loaded.password_hash == "argon2id$test"
    assert loaded.role is UserRole.ADMIN


def test_session_repository_filters_expired_and_revoked_sessions(uow_factory):
    now = datetime.now(timezone.utc)
    active = AuthSessionRecord(
        token_hash="active",
        user_id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        last_seen_at=now,
    )
    expired = active.model_copy(
        update={
            "session_id": uuid4(),
            "token_hash": "expired",
            "expires_at": now - timedelta(seconds=1),
        }
    )

    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=active.user_id, created_at=now))
        uow.sessions.add(active)
        uow.sessions.add(expired)
        uow.commit()

    with uow_factory() as uow:
        assert uow.sessions.get_active_by_token_hash("active", now) == active
        assert uow.sessions.get_active_by_token_hash("expired", now) is None
        assert uow.sessions.revoke(active.session_id, now) is True
        uow.commit()

    with uow_factory() as uow:
        assert uow.sessions.get_active_by_token_hash("active", now) is None


def test_audit_repository_persists_failed_login_without_sensitive_metadata(uow_factory):
    event = AuditEventRecord(
        action=AuditAction.LOGIN_FAILED,
        success=False,
        request_id="req-1",
        occurred_at=datetime.now(timezone.utc),
        metadata_json={"email_domain": "example.com"},
    )

    with uow_factory() as uow:
        saved = uow.audit_events.add(event)
        uow.commit()

    assert saved.event_id == event.event_id
