from typing import BinaryIO, Protocol

from .errors import StorageError, StorageErrorCode
from .keys import dataset_key, normalize_filename, task_file_key, validate_key
from .local import LocalFileStorage
from .models import StorageObject

class Storage(Protocol):
    def put(
        self,
        stream: BinaryIO,
        *,
        key: str,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StorageObject:
        ...

    def get(self, uri: str) -> BinaryIO:
        ...

    def delete(self, uri: str) -> None:
        ...

    def exists(self, uri: str) -> bool:
        ...

    def stat(self, uri: str) -> StorageObject:
        ...

    def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
        ...


from .access import FileAccessDeniedError, FileAccessService, artifact_content_url
from .artifacts import ArtifactStorageService
from .lifecycle import StorageConsistencyIssue, StorageLifecycleService

__all__ = [
    "Storage",
    "LocalFileStorage",
    "StorageError",
    "StorageErrorCode",
    "StorageObject",
    "ArtifactStorageService",
    "FileAccessDeniedError",
    "FileAccessService",
    "artifact_content_url",
    "StorageConsistencyIssue",
    "StorageLifecycleService",
    "dataset_key",
    "normalize_filename",
    "task_file_key",
    "validate_key",
]
