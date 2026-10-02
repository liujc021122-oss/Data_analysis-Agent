from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from threading import Lock
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
        self._timestamp_lock = Lock()
        self._last_occurred_at: datetime | None = None

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

    def _event(
        self,
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
            occurred_at=self._next_occurred_at(),
            metadata_json=self._safe_metadata(metadata),
        )

    def _next_occurred_at(self) -> datetime:
        candidate = utc_now()
        with self._timestamp_lock:
            if (
                self._last_occurred_at is not None
                and candidate <= self._last_occurred_at
            ):
                candidate = self._last_occurred_at + timedelta(microseconds=1)
            self._last_occurred_at = candidate
        return candidate

    @staticmethod
    def _safe_metadata(
        metadata: Mapping[str, str | int | bool] | None,
    ) -> dict[str, str | int | bool]:
        if metadata is None:
            return {}
        safe: dict[str, str | int | bool] = {}
        for key, value in metadata.items():
            if key not in _ALLOWED_METADATA_KEYS or not isinstance(
                value, _SCALAR_TYPES
            ):
                continue
            if key == "email_domain":
                # Keep the field useful for coarse monitoring without persisting
                # any caller-controlled email text that could contain a secret.
                safe[key] = "provided" if value else "invalid"
            else:
                safe[key] = value
        return safe


__all__ = ["AuditWriter"]
