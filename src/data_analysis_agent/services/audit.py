from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from ..domain.enums import AuditAction
from ..persistence.models import AuditEventRecord, UserRecord
from ..persistence.unit_of_work import UnitOfWork
from ..domain.models import utc_now


_ALLOWED_METADATA_KEYS = frozenset(
    {"email_domain", "resource_type", "role", "status_code", "reason_code"}
)
_SCALAR_TYPES = (str, int, bool)


class AuditWriter:
    """Persist bounded audit events without accepting sensitive metadata."""

    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    def record(
        self,
        *,
        action: AuditAction,
        user_id: UUID | None,
        request_id: str,
        target_type: str | None = None,
        target_id: UUID | None = None,
        success: bool,
        metadata: Mapping[str, str | int | bool] | None = None,
    ) -> AuditEventRecord:
        with self._uow_factory() as uow:
            saved = self.record_in_uow(
                uow,
                action=action,
                user_id=user_id,
                request_id=request_id,
                target_type=target_type,
                target_id=target_id,
                success=success,
                metadata=metadata,
            )
            uow.commit()
            return saved

    def record_in_uow(
        self,
        uow: UnitOfWork,
        *,
        action: AuditAction,
        user_id: UUID | None,
        request_id: str,
        target_type: str | None = None,
        target_id: UUID | None = None,
        success: bool,
        metadata: Mapping[str, str | int | bool] | None = None,
    ) -> AuditEventRecord:
        event = self._event(
            action=action,
            user_id=user_id,
            request_id=request_id,
            target_type=target_type,
            target_id=target_id,
            success=success,
            metadata=metadata,
        )
        if user_id is not None:
            uow.users.ensure(UserRecord(user_id=user_id, created_at=event.occurred_at))
        return uow.audit_events.add(event)

    def try_record(self, **kwargs: Any) -> AuditEventRecord | None:
        """Write an event without changing the public result on audit failure."""
        try:
            return self.record(**kwargs)
        except Exception:
            return None

    @classmethod
    def _event(
        cls,
        *,
        action: AuditAction,
        user_id: UUID | None,
        request_id: str,
        target_type: str | None,
        target_id: UUID | None,
        success: bool,
        metadata: Mapping[str, str | int | bool] | None,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            success=success,
            request_id=request_id,
            occurred_at=utc_now(),
            metadata_json=cls._safe_metadata(metadata),
        )

    @staticmethod
    def _safe_metadata(
        metadata: Mapping[str, str | int | bool] | None,
    ) -> dict[str, str | int | bool]:
        if metadata is None:
            return {}
        return {
            key: value
            for key, value in metadata.items()
            if key in _ALLOWED_METADATA_KEYS and isinstance(value, _SCALAR_TYPES)
        }


__all__ = ["AuditWriter"]
