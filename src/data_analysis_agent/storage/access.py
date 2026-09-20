from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from .artifacts import ArtifactRepository
from .errors import StorageError

if TYPE_CHECKING:
    from . import Storage


class FileAccessDeniedError(Exception):
    code = "FILE_ACCESS_DENIED"

    def __init__(self, message: str = "file is not available for this user") -> None:
        self.message = message
        super().__init__(message)


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
        if record is None or not record.file_path:
            raise FileAccessDeniedError()
        try:
            return self._storage.create_download_url(
                record.file_path,
                expires_in=expires_in,
            )
        except StorageError as exc:
            raise FileAccessDeniedError() from exc
