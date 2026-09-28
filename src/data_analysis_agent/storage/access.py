from __future__ import annotations

from typing import BinaryIO, TYPE_CHECKING
from urllib.parse import urlencode
from uuid import UUID

from .artifacts import ArtifactRepository
from .errors import StorageError

if TYPE_CHECKING:
    from ..persistence.models import ArtifactRecord
    from . import Storage


class FileAccessDeniedError(Exception):
    code = "FILE_ACCESS_DENIED"

    def __init__(self, message: str = "file is not available for this user") -> None:
        self.message = message
        super().__init__(message)


def artifact_content_url(artifact_id: UUID, download_url: str) -> str:
    """Map local signed tokens to the protected API content endpoint."""
    if download_url.startswith("local-download://"):
        query = urlencode({"download_url": download_url})
        return f"/api/artifacts/{artifact_id}/content?{query}"
    return download_url


class FileAccessService:
    def __init__(
        self,
        *,
        storage: Storage,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._storage = storage
        self._artifact_repository = artifact_repository

    def create_download_url(
        self,
        artifact_id: UUID,
        user_id: UUID,
        expires_in: int = 300,
    ) -> str:
        record = self._artifact_repository.get_for_user(artifact_id, user_id)
        try:
            stored = self._validated_record(record)
            return self._storage.create_download_url(
                record.file_path,
                expires_in=expires_in,
            )
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def open_download(
        self,
        artifact_id: UUID,
        user_id: UUID,
        download_url: str,
    ) -> tuple["ArtifactRecord", BinaryIO]:
        """Open a signed download token after rechecking its artifact owner."""
        record = self._artifact_repository.get_for_user(artifact_id, user_id)
        try:
            stored = self._validated_record(record)
            requested = self._storage.stat(download_url)
            if requested.uri != stored.uri or requested.key != stored.key:
                raise FileAccessDeniedError()
            return record, self._storage.get(download_url)
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def _validated_record(self, record: "ArtifactRecord | None"):
        if record is None or not record.file_path:
            raise FileAccessDeniedError()
        try:
            stored = self._storage.stat(record.file_path)
        except StorageError as exc:
            raise FileAccessDeniedError() from exc
        if (
            stored.size_bytes != record.size_bytes
            or (
                record.content_hash is not None
                and stored.checksum != record.content_hash
            )
        ):
            raise FileAccessDeniedError()
        return stored
