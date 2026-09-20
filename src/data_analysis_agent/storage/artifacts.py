from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, Sequence
from uuid import UUID, uuid4

from ..domain.models import utc_now
from ..persistence.models import ArtifactRecord, ReportRecord
from .errors import StorageError, StorageErrorCode
from .keys import normalize_filename, task_file_key
from .models import StorageObject

if TYPE_CHECKING:
    from . import Storage


class ArtifactRepository(Protocol):
    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        ...

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        ...

    def list_for_task(self, task_id: UUID) -> list[ArtifactRecord]:
        ...

    def get_for_user(self, artifact_id: UUID, user_id: UUID) -> ArtifactRecord | None:
        ...


class ReportRepository(Protocol):
    def add(self, record: ReportRecord) -> ReportRecord:
        ...


class ArtifactStorageService:
    """Store staged task files and persist only provider-neutral metadata."""

    def __init__(
        self,
        *,
        storage: Storage,
        artifact_repository: ArtifactRepository,
        report_repository: ReportRepository | None = None,
    ) -> None:
        self._storage = storage
        self._artifact_repository = artifact_repository
        self._report_repository = report_repository

    def store_file(
        self,
        *,
        task_id: UUID,
        source_path: Path,
        artifact_type: Literal["CHART", "REPORT"],
        filename: str,
        mime_type: str,
        format: str | None = None,
        title: str | None = None,
        description: str | None = None,
        source_tool_call_id: UUID | None = None,
    ) -> ArtifactRecord:
        artifact_id = uuid4()
        safe_name = normalize_filename(filename)
        kind = self._storage_kind(artifact_type)
        key = task_file_key(task_id, artifact_id, kind, safe_name)
        stored = self._put_source(
            source_path,
            key=key,
            content_type=mime_type,
        )
        record = ArtifactRecord(
            artifact_id=artifact_id,
            task_id=task_id,
            artifact_type=artifact_type,
            name=safe_name,
            file_path=stored.uri,
            format=self._format_value(format),
            mime_type=mime_type,
            content_hash=stored.checksum,
            size_bytes=stored.size_bytes,
            description=description,
            title=title,
            source_tool_call_id=source_tool_call_id,
            created_at=utc_now(),
        )
        try:
            return self._artifact_repository.add(record)
        except Exception as exc:
            self._delete_after_persistence_failure(stored.uri)
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to persist artifact metadata",
            ) from exc

    def store_report(
        self,
        *,
        task_id: UUID,
        source_path: Path,
        filename: str,
        mime_type: str,
        format: str,
        title: str | None = None,
        description: str | None = None,
        source_tool_call_id: UUID | None = None,
    ) -> tuple[ArtifactRecord, ReportRecord]:
        if self._report_repository is None:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "report metadata repository is not configured",
            )
        artifact = self.store_file(
            task_id=task_id,
            source_path=source_path,
            artifact_type="REPORT",
            filename=filename,
            mime_type=mime_type,
            format=format,
            title=title,
            description=description,
            source_tool_call_id=source_tool_call_id,
        )
        report = ReportRecord(
            report_id=uuid4(),
            artifact_id=artifact.artifact_id,
            task_id=task_id,
            format=self._format_value(format) or format,
            storage_uri=artifact.file_path or "",
            size_bytes=artifact.size_bytes,
            content_hash=artifact.content_hash,
            created_at=artifact.created_at,
        )
        try:
            persisted_report = self._report_repository.add(report)
        except Exception as exc:
            if artifact.file_path:
                self._delete_after_persistence_failure(artifact.file_path)
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to persist report metadata",
            ) from exc
        return artifact, persisted_report

    def delete_task_files(
        self,
        task_id: UUID,
        records: Sequence[ArtifactRecord],
    ) -> None:
        deleted: set[str] = set()
        for record in records:
            if record.task_id != task_id or not record.file_path:
                continue
            if record.file_path in deleted:
                continue
            self._storage.delete(record.file_path)
            deleted.add(record.file_path)

    def verify(self, record: ArtifactRecord) -> StorageObject:
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
            record.size_bytes != current.size_bytes
            or (
                record.content_hash is not None
                and record.content_hash != current.checksum
            )
        ):
            raise StorageError(
                StorageErrorCode.METADATA_MISMATCH,
                "stored artifact does not match persisted metadata",
            )
        return current

    @staticmethod
    def _storage_kind(artifact_type: str) -> Literal["chart", "report"]:
        if artifact_type == "CHART":
            return "chart"
        if artifact_type == "REPORT":
            return "report"
        raise ValueError("artifact_type must be CHART or REPORT")

    def _put_source(
        self,
        source_path: Path,
        *,
        key: str,
        content_type: str,
    ) -> StorageObject:
        try:
            with source_path.open("rb") as source:
                return self._storage.put(
                    source,
                    key=key,
                    content_type=content_type,
                )
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to read artifact source",
            ) from exc

    def _delete_after_persistence_failure(self, uri: str) -> None:
        try:
            self._storage.delete(uri)
        except Exception:
            pass

    @staticmethod
    def _format_value(format: str | None) -> str | None:
        if format is None:
            return None
        return getattr(format, "value", format)
