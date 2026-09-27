from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request


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
