from dataclasses import dataclass
from uuid import UUID

from ..domain.enums import UserRole


@dataclass(frozen=True)
class AccessSubject:
    user_id: UUID
    role: UserRole

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN


class AuthorizationService:
    @staticmethod
    def can_access_owner(subject: AccessSubject, owner_id: UUID) -> bool:
        return subject.is_admin or subject.user_id == owner_id

    @staticmethod
    def require_admin(subject: AccessSubject) -> None:
        if not subject.is_admin:
            raise PermissionError("administrator role is required")

