from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import secrets
from uuid import UUID

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2 import Type
from argon2.exceptions import VerificationError

from ..config.settings import Settings
from ..domain.enums import AuditAction, UserRole
from ..domain.models import utc_now
from ..persistence.models import AuditEventRecord, AuthSessionRecord, UserRecord


class AuthenticationError(Exception):
    """Base class for stable authentication service failures."""


class EmailAlreadyRegisteredError(AuthenticationError):
    """The normalized email already belongs to a registered account."""


class InvalidEmailError(AuthenticationError):
    """The supplied email cannot be normalized into an account identifier."""


class InvalidCredentialsError(AuthenticationError):
    """The credentials do not identify an active account."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class LoginResult:
    user: AuthenticatedUser
    token: str
    expires_at: datetime


class PasswordHasher:
    _hasher = Argon2PasswordHasher(type=Type.ID)

    @staticmethod
    def hash(password: str) -> str:
        return PasswordHasher._hasher.hash(password)

    @staticmethod
    def verify(password: str, encoded_hash: str) -> bool:
        try:
            return PasswordHasher._hasher.verify(encoded_hash, password)
        except (VerificationError, TypeError, ValueError):
            return False


class AuthenticationService:
    def __init__(self, uow_factory, settings: Settings):
        self.uow_factory = uow_factory
        self.settings = settings

    def register(
        self, *, email: str, password: str, request_id: str | None
    ) -> AuthenticatedUser:
        normalized_email = _normalize_email(email, invalid_credentials=False)
        role = (
            UserRole.ADMIN
            if normalized_email in self.settings.auth_admin_emails
            else UserRole.USER
        )
        now = utc_now()
        user = UserRecord(
            email_normalized=normalized_email,
            password_hash=PasswordHasher.hash(password),
            role=role,
            is_active=True,
            created_at=now,
        )
        try:
            with self.uow_factory() as uow:
                if uow.users.get_by_email(normalized_email) is not None:
                    raise EmailAlreadyRegisteredError()
                saved = uow.users.ensure(user)
                authenticated = _to_authenticated_user(saved)
                uow.audit_events.add(
                    _audit_event(
                        action=AuditAction.REGISTERED,
                        user_id=authenticated.user_id,
                        request_id=request_id,
                        success=True,
                        metadata={"role": authenticated.role.value},
                        occurred_at=now,
                    )
                )
                uow.commit()
                return authenticated
        except EmailAlreadyRegisteredError:
            raise
        except Exception as exc:
            # A concurrent unique-index conflict must not expose provider details.
            if _is_integrity_error(exc):
                raise EmailAlreadyRegisteredError() from exc
            raise

    def login(
        self, *, email: str, password: str, request_id: str | None
    ) -> LoginResult:
        try:
            normalized_email = _normalize_email(email, invalid_credentials=True)
        except InvalidCredentialsError:
            self._record_failed_login(email=email, request_id=request_id)
            raise

        now = utc_now()
        with self.uow_factory() as uow:
            user = uow.users.get_by_email(normalized_email)
            if (
                user is None
                or not user.is_active
                or user.password_hash is None
                or not PasswordHasher.verify(password, user.password_hash)
            ):
                if user is not None:
                    user_id = user.user_id
                else:
                    user_id = None
                uow.audit_events.add(
                    _audit_event(
                        action=AuditAction.LOGIN_FAILED,
                        user_id=user_id,
                        request_id=request_id,
                        success=False,
                        metadata={"email_domain": _email_domain(email)},
                        occurred_at=now,
                    )
                )
                uow.commit()
                raise InvalidCredentialsError()

            token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=self.settings.session_ttl_seconds)
            uow.sessions.add(
                AuthSessionRecord(
                    token_hash=_token_hash(token),
                    user_id=user.user_id,
                    created_at=now,
                    expires_at=expires_at,
                    last_seen_at=now,
                )
            )
            authenticated = _to_authenticated_user(user)
            uow.audit_events.add(
                _audit_event(
                    action=AuditAction.LOGIN_SUCCEEDED,
                    user_id=authenticated.user_id,
                    request_id=request_id,
                    success=True,
                    metadata={"role": authenticated.role.value},
                    occurred_at=now,
                )
            )
            uow.commit()
            return LoginResult(
                user=authenticated,
                token=token,
                expires_at=expires_at,
            )

    def authenticate_session(
        self, *, token: str, now: datetime | None = None
    ) -> AuthenticatedUser | None:
        if not token:
            return None
        current_time = now or utc_now()
        with self.uow_factory() as uow:
            session = uow.sessions.get_active_by_token_hash(
                _token_hash(token), current_time
            )
            if session is None:
                return None
            user = uow.users.get(session.user_id)
            if user is None or not user.is_active:
                return None
            uow.sessions.touch(session.session_id, current_time)
            uow.commit()
            return _to_authenticated_user(user)

    def logout(self, *, token: str, request_id: str | None) -> bool:
        if not token:
            return False
        now = utc_now()
        with self.uow_factory() as uow:
            session = uow.sessions.get_active_by_token_hash(_token_hash(token), now)
            if session is None:
                return False
            revoked = uow.sessions.revoke(session.session_id, now)
            if not revoked:
                return False
            uow.audit_events.add(
                _audit_event(
                    action=AuditAction.LOGGED_OUT,
                    user_id=session.user_id,
                    request_id=request_id,
                    success=True,
                    metadata={},
                    occurred_at=now,
                )
            )
            uow.commit()
            return True

    def _record_failed_login(self, *, email: str, request_id: str | None) -> None:
        with self.uow_factory() as uow:
            uow.audit_events.add(
                _audit_event(
                    action=AuditAction.LOGIN_FAILED,
                    user_id=None,
                    request_id=request_id,
                    success=False,
                    metadata={"email_domain": _email_domain(email)},
                    occurred_at=utc_now(),
                )
            )
            uow.commit()


def _normalize_email(email: str, *, invalid_credentials: bool) -> str:
    normalized = email.strip().lower() if isinstance(email, str) else ""
    local, separator, domain = normalized.partition("@")
    valid = bool(separator and local.strip() and domain.strip() and "@" not in domain)
    if not valid:
        if invalid_credentials:
            raise InvalidCredentialsError()
        raise InvalidEmailError()
    return normalized


def _email_domain(email: str) -> str:
    if not isinstance(email, str):
        return "invalid"
    _, separator, domain = email.strip().lower().partition("@")
    return domain if separator and domain.strip() else "invalid"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _to_authenticated_user(record: UserRecord) -> AuthenticatedUser:
    if record.email_normalized is None:
        raise InvalidCredentialsError()
    return AuthenticatedUser(
        user_id=record.user_id,
        email=record.email_normalized,
        role=record.role,
        is_active=record.is_active,
        created_at=record.created_at,
    )


def _audit_event(
    *,
    action: AuditAction,
    user_id: UUID | None,
    request_id: str | None,
    success: bool,
    metadata: dict[str, str],
    occurred_at: datetime,
) -> AuditEventRecord:
    return AuditEventRecord(
        user_id=user_id,
        action=action,
        success=success,
        request_id=request_id or "unknown",
        occurred_at=occurred_at,
        metadata_json=metadata,
    )


def _is_integrity_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "IntegrityError"
