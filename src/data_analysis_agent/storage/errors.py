from enum import Enum
from typing import Any, Mapping


class StorageErrorCode(str, Enum):
    INVALID_KEY = "INVALID_KEY"
    INVALID_URI = "INVALID_URI"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    DOWNLOAD_URL_INVALID = "DOWNLOAD_URL_INVALID"
    DOWNLOAD_URL_EXPIRED = "DOWNLOAD_URL_EXPIRED"
    METADATA_MISMATCH = "METADATA_MISMATCH"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"


class StorageError(Exception):
    """Storage failure with a stable code and structured details."""

    def __init__(
        self,
        code: StorageErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(self.message)
