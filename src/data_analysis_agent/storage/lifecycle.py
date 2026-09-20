from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..domain.models import utc_now
from ..persistence.models import ArtifactRecord
from .artifacts import ArtifactStorageService
from .errors import StorageError, StorageErrorCode
from .models import StorageObject


class StorageConsistencyIssue(BaseModel):
    """A path-free description of a persisted artifact/storage mismatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    task_id: UUID
    code: str
    message: str


class StorageLifecycleService:
    """Own cleanup, retention, and reconciliation for task-scoped objects."""

    def __init__(
        self,
        *,
        storage,
        artifact_storage: ArtifactStorageService | None = None,
        output_root: str | Path | None = None,
        retention_days: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or retention_days <= 0
        ):
            raise ValueError("retention_days must be a positive integer")
        self._storage = storage
        self._artifact_storage = artifact_storage
        self._output_root = (
            Path(output_root).resolve(strict=False) if output_root is not None else None
        )
        self._retention_days = retention_days
        self._clock = clock or utc_now

    def cleanup_failed_task(
        self,
        *,
        task_id: UUID,
        staging_dir: Path | None,
        records: Sequence[ArtifactRecord],
    ) -> None:
        """Best-effort cleanup for a failed task, with safe staging deletion."""
        deletion_error = self._delete_records(task_id=task_id, records=records)
        staging_error = None
        if staging_dir is not None:
            try:
                self._remove_staging_dir(staging_dir)
            except StorageError as exc:
                staging_error = exc

        if deletion_error is not None:
            if staging_error is not None:
                logging.getLogger(__name__).warning(
                    "Task storage cleanup had object and staging failures"
                )
            raise deletion_error
        if staging_error is not None:
            raise staging_error

    def delete_task(
        self,
        *,
        task_id: UUID,
        records: Sequence[ArtifactRecord],
    ) -> None:
        """Delete task objects idempotently without touching other task keys."""
        error = self._delete_records(task_id=task_id, records=records)
        if error is not None:
            raise error

    def reconcile(
        self,
        records: Sequence[ArtifactRecord],
    ) -> list[StorageConsistencyIssue]:
        """Return stable, path-free issues for missing or mismatched objects."""
        issues: list[StorageConsistencyIssue] = []
        for record in records:
            try:
                self._verify(record)
            except StorageError as exc:
                issues.append(
                    StorageConsistencyIssue(
                        artifact_id=record.artifact_id,
                        task_id=record.task_id,
                        code=self._code_value(exc.code),
                        message=self._issue_message(exc.code),
                    )
                )
            except Exception:
                issues.append(
                    StorageConsistencyIssue(
                        artifact_id=record.artifact_id,
                        task_id=record.task_id,
                        code=StorageErrorCode.BACKEND_UNAVAILABLE.value,
                        message="storage backend is unavailable",
                    )
                )
        return issues

    def cleanup_expired(
        self,
        records: Sequence[ArtifactRecord],
        *,
        now: datetime | None = None,
    ) -> list[ArtifactRecord]:
        """Delete objects strictly older than the configured retention cutoff."""
        current_time = self._as_utc(now or self._clock())
        cutoff = current_time - timedelta(days=self._retention_days)
        deleted: list[ArtifactRecord] = []
        seen_uris: set[str] = set()
        failures = 0

        for record in records:
            if record.created_at >= cutoff or not record.file_path:
                continue
            if record.file_path in seen_uris:
                continue
            seen_uris.add(record.file_path)
            try:
                self._storage.delete(record.file_path)
            except StorageError as exc:
                if self._code_value(exc.code) != StorageErrorCode.OBJECT_NOT_FOUND.value:
                    failures += 1
                    continue
            except Exception:
                failures += 1
                continue
            deleted.append(record)

        if failures:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to delete expired storage objects",
                details={"failed_objects": failures},
            )
        return deleted

    def cleanup_retention(
        self,
        records: Sequence[ArtifactRecord],
        *,
        now: datetime | None = None,
    ) -> list[ArtifactRecord]:
        """Compatibility alias for retention cleanup callers."""
        return self.cleanup_expired(records, now=now)

    def _delete_records(
        self,
        *,
        task_id: UUID,
        records: Sequence[ArtifactRecord],
    ) -> StorageError | None:
        seen_uris: set[str] = set()
        failures = 0
        for record in records:
            if record.task_id != task_id or not record.file_path:
                continue
            if record.file_path in seen_uris:
                continue
            seen_uris.add(record.file_path)
            try:
                self._storage.delete(record.file_path)
            except StorageError as exc:
                if self._code_value(exc.code) == StorageErrorCode.OBJECT_NOT_FOUND.value:
                    continue
                failures += 1
            except Exception:
                failures += 1

        if failures:
            return StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to delete task storage objects",
                details={"failed_objects": failures},
            )
        return None

    def _remove_staging_dir(self, staging_dir: Path) -> None:
        if self._output_root is None:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "output root is required for staging cleanup",
            )
        try:
            candidate = Path(staging_dir).resolve(strict=False)
            candidate.relative_to(self._output_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "staging directory must remain within the output root",
            ) from exc
        if candidate == self._output_root:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "staging directory cannot be the output root",
            )
        if not candidate.exists():
            return
        if not candidate.is_dir():
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "staging path is not a directory",
            )
        try:
            shutil.rmtree(candidate)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to remove staging directory",
            ) from exc

    def _verify(self, record: ArtifactRecord) -> StorageObject:
        if self._artifact_storage is not None:
            return self._artifact_storage.verify(record)
        if not record.file_path:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "artifact storage reference is invalid",
            )
        if not self._storage.exists(record.file_path):
            raise StorageError(
                StorageErrorCode.OBJECT_NOT_FOUND,
                "stored artifact was not found",
            )
        current = self._storage.stat(record.file_path)
        if (
            current.size_bytes != record.size_bytes
            or (
                record.content_hash is not None
                and current.checksum != record.content_hash
            )
        ):
            raise StorageError(
                StorageErrorCode.METADATA_MISMATCH,
                "stored artifact does not match persisted metadata",
            )
        return current

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lifecycle clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _code_value(code: object) -> str:
        return getattr(code, "value", str(code))

    @staticmethod
    def _issue_message(code: object) -> str:
        code_value = StorageLifecycleService._code_value(code)
        return {
            StorageErrorCode.OBJECT_NOT_FOUND.value: "stored artifact is missing",
            StorageErrorCode.METADATA_MISMATCH.value: "stored artifact metadata does not match",
            StorageErrorCode.INVALID_URI.value: "stored artifact reference is invalid",
            StorageErrorCode.BACKEND_UNAVAILABLE.value: "storage backend is unavailable",
        }.get(code_value, "stored artifact could not be verified")
