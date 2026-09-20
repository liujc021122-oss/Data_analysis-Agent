from enum import Enum
from typing import Any, Mapping


class DatasetErrorCode(str, Enum):
    INVALID_DATASET_REQUEST = "INVALID_DATASET_REQUEST"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    EMPTY_FILE = "EMPTY_FILE"
    ENCODING_DETECTION_FAILED = "ENCODING_DETECTION_FAILED"
    INVALID_CSV = "INVALID_CSV"
    DUPLICATE_COLUMNS = "DUPLICATE_COLUMNS"
    EMPTY_COLUMN_NAME = "EMPTY_COLUMN_NAME"
    CONTROL_CHARACTER = "CONTROL_CHARACTER"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    STORAGE_OBJECT_NOT_FOUND = "STORAGE_OBJECT_NOT_FOUND"
    STORAGE_METADATA_MISMATCH = "STORAGE_METADATA_MISMATCH"
    DATASET_PERSISTENCE_FAILURE = "DATASET_PERSISTENCE_FAILURE"
    DATASET_ACCESS_DENIED = "DATASET_ACCESS_DENIED"


class DatasetError(Exception):
    """Base error with a stable code and structured, non-message details."""

    def __init__(
        self,
        code: DatasetErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(self.message)


class StorageError(DatasetError):
    pass


class UploadValidationError(DatasetError):
    pass


class DatasetPersistenceError(DatasetError):
    pass


class DatasetAccessDeniedError(DatasetError):
    pass


from data_analysis_agent.storage.errors import StorageErrorCode
