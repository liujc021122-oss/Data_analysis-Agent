from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import unquote, urlsplit

from .errors import DatasetErrorCode, StorageError
from .models import StoredObject


class StorageBackend(Protocol):
    def put_stream(self, stream: BinaryIO, *, key: str, max_bytes: int) -> StoredObject:
        ...

    def open(self, uri: str) -> BinaryIO:
        ...

    def delete(self, uri: str) -> None:
        ...


class LocalStorageBackend:
    _CHUNK_SIZE = 64 * 1024

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve(strict=False)

    def put_stream(self, stream: BinaryIO, *, key: str, max_bytes: int) -> StoredObject:
        if max_bytes <= 0:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "max_bytes must be positive",
            )

        destination = self._path_for_key(key)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            digest = hashlib.sha256()
            with destination.open("wb") as output:
                while chunk := stream.read(self._CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise StorageError(
                            DatasetErrorCode.FILE_TOO_LARGE,
                            "stream exceeds max_bytes",
                        )
                    output.write(chunk)
                    digest.update(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        return StoredObject(
            uri=f"local://{key}",
            size_bytes=size,
            checksum=f"sha256:{digest.hexdigest()}",
        )

    def open(self, uri: str) -> BinaryIO:
        path = self._path_for_uri(uri)
        try:
            return path.open("rb")
        except Exception as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unable to open stored object",
            ) from exc

    def delete(self, uri: str) -> None:
        path = self._path_for_uri(uri)
        try:
            path.unlink()
        except Exception as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unable to delete stored object",
            ) from exc

    def _path_for_uri(self, uri: str) -> Path:
        parsed = urlsplit(uri)
        if parsed.scheme != "local" or parsed.query or parsed.fragment:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unsupported storage URI scheme",
            )
        if not parsed.netloc or not parsed.path:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "invalid storage URI path",
            )
        key = f"{parsed.netloc}{parsed.path}"
        return self._path_for_key(unquote(key))

    def _path_for_key(self, key: str) -> Path:
        candidate = Path(key)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "path must remain within local storage root",
            )

        resolved = (self._root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "path must remain within local storage root",
            ) from exc
        return resolved
