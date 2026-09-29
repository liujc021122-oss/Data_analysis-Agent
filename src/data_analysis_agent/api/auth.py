from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request

from ..domain.enums import UserRole
from ..services.auth import AuthenticationService


def get_current_principal(request: Request) -> Principal:
    provider = getattr(request.app.state.api_application, "principal_provider", None)
    if provider is None:
        raise AuthenticationError("authentication is required")
    return provider.current_principal(request)


class AuthenticationError(ValueError):
    """Raised when a request does not carry a valid authenticated principal."""


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    role: UserRole = UserRole.USER

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN


class PrincipalProvider(Protocol):
    def current_principal(self, request: Request) -> Principal:
        ...


class HeaderPrincipalProvider:
    """Development and test principal provider based on ``X-User-ID``."""

    def current_principal(self, request: Request) -> Principal:
        raw = request.headers.get("X-User-ID", "").strip()
        try:
            return Principal(user_id=UUID(raw))
        except (ValueError, AttributeError) as exc:
            raise AuthenticationError("authentication is required") from exc


class SessionPrincipalProvider:
    """Resolve the current principal from the configured opaque session cookie."""

    def __init__(self, auth_service: AuthenticationService):
        self.auth_service = auth_service

    def current_principal(self, request: Request) -> Principal:
        token = request.cookies.get(self.auth_service.settings.session_cookie_name)
        if not token:
            raise AuthenticationError("authentication is required")
        user = self.auth_service.authenticate_session(token=token)
        if user is None:
            raise AuthenticationError("authentication is required")
        return Principal(user_id=user.user_id, role=user.role)
