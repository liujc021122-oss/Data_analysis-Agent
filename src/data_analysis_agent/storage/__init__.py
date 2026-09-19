from typing import BinaryIO, Protocol

from .errors import StorageError, StorageErrorCode
from .keys import dataset_key, normalize_filename, task_file_key, validate_key
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

__all__ = [
    "Storage",
    "StorageError",
    "StorageErrorCode",
    "StorageObject",
    "dataset_key",
    "normalize_filename",
    "task_file_key",
    "validate_key",
]
