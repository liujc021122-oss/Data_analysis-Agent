from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request


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
