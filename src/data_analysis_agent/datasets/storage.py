from __future__ import annotations

from typing import BinaryIO, Protocol

from ..storage.errors import StorageError as CanonicalStorageError
from ..storage.errors import StorageErrorCode
from ..storage.local import LocalFileStorage
from .errors import DatasetErrorCode, StorageError
from .models import StoredObject


class StorageBackend(Protocol):
    def put_stream(self, stream: BinaryIO, *, key: str, max_bytes: int) -> StoredObject:
        ...

    def open(self, uri: str) -> BinaryIO:
        ...

    def delete(self, uri: str) -> None:
        ...


class LocalStorageBackend(LocalFileStorage):
    """M04 compatibility facade over the canonical local storage adapter."""

    def put_stream(self, stream: BinaryIO, *, key: str, max_bytes: int) -> StoredObject:
        if max_bytes <= 0:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "max_bytes must be positive",
            )

        try:
            return self.put(
                stream,
                key=key,
                content_type="text/csv",
                max_bytes=max_bytes,
            )
        except CanonicalStorageError as exc:
            if exc.code is StorageErrorCode.FILE_TOO_LARGE:
                raise StorageError(
                    DatasetErrorCode.FILE_TOO_LARGE,
                    "stream exceeds max_bytes",
                ) from exc
            if exc.code is StorageErrorCode.INVALID_KEY:
                raise StorageError(
                    DatasetErrorCode.STORAGE_FAILURE,
                    "path must remain within local storage root",
                ) from exc
            if exc.code is StorageErrorCode.BACKEND_UNAVAILABLE:
                raise StorageError(
                    DatasetErrorCode.STORAGE_FAILURE,
                    "unable to write stored object",
                ) from exc
            raise

    def open(self, uri: str) -> BinaryIO:
        try:
            return self.get(uri)
        except CanonicalStorageError as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unable to open stored object",
            ) from exc
