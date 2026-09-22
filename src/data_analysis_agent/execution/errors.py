from __future__ import annotations

from enum import Enum
import re
from typing import ClassVar, Iterable
from uuid import UUID


MAX_SAFE_TEXT_LENGTH = 16_384
_TRUNCATION_SUFFIX = "...[truncated]"

_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")
_CREDENTIAL_URL_RE = re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|token|key|sig|signature)=)[^&#\s]+"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|token|key|sig|signature|openai_api_key|database_url|redis_url|storage_secret_access_key)\b\s*[:=]\s*)(['\"]?)[^,\s'\";)}]+"
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/])[^<>\"'\r\n;]+")
_UNC_PATH_RE = re.compile(r"(?i)(?<![\w])\\\\[^\\/\s]+[\\/][^<>\"'\r\n;]+")
_UNIX_PATH_RE = re.compile(r"(?<![\w])/(?:[^/\s;,'\"]+/)*[^/\s;,'\"]+")


def truncate_execution_text(value: object, *, limit: int = MAX_SAFE_TEXT_LENGTH) -> str:
    """Return bounded text suitable for execution feedback and diagnostics."""
    if limit <= 0:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_SUFFIX):
        return _TRUNCATION_SUFFIX[:limit]
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def sanitize_execution_text(
    value: object,
    *,
    secrets: Iterable[object] = (),
    limit: int = MAX_SAFE_TEXT_LENGTH,
) -> str:
    """Redact common credentials and host paths before bounding text.

    The helper intentionally returns generic markers instead of preserving
    path basenames or provider error payloads. It is used for diagnostics,
    never for code or user data that must be executed.
    """
    text = str(value)
    for secret in sorted(
        (str(secret) for secret in secrets if secret is not None and str(secret)),
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _CREDENTIAL_URL_RE.sub(r"\1[REDACTED]@", text)
    text = _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)
    text = _UNC_PATH_RE.sub("[PATH]", text)
    text = _WINDOWS_PATH_RE.sub("[PATH]", text)
    text = _UNIX_PATH_RE.sub("[PATH]", text)
    return truncate_execution_text(text, limit=limit)


class ExecutionErrorCode(str, Enum):
    CONFIGURATION_MISSING = "CONFIGURATION_MISSING"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    FILE_LIMIT = "FILE_LIMIT"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    NETWORK_DENIED = "NETWORK_DENIED"
    CONTAINER_FAILURE = "CONTAINER_FAILURE"
    CLEANUP_FAILURE = "CLEANUP_FAILURE"


class ExecutionError(Exception):
    code: ClassVar[str] = "EXECUTION_ERROR"
    default_message: ClassVar[str] = "code execution failed"

    def __init__(
        self,
        message: object | None = None,
        *,
        task_id: UUID | None = None,
        secrets: Iterable[object] = (),
    ) -> None:
        self.task_id = task_id
        self.message = sanitize_execution_text(
            self.default_message if message is None else message,
            secrets=secrets,
        )
        super().__init__(f"{self.code}: {self.message}")


class ConfigurationMissingError(ExecutionError):
    code = ExecutionErrorCode.CONFIGURATION_MISSING.value
    default_message = "execution configuration is missing"


class BackendUnavailableError(ExecutionError):
    code = ExecutionErrorCode.BACKEND_UNAVAILABLE.value
    default_message = "execution backend is unavailable"


class ExecutionTimeoutError(ExecutionError):
    code = ExecutionErrorCode.TIMEOUT.value
    default_message = "code execution timed out"


class ResourceLimitError(ExecutionError):
    code = ExecutionErrorCode.RESOURCE_LIMIT.value
    default_message = "code execution reached a resource limit"


class OutputLimitError(ExecutionError):
    code = ExecutionErrorCode.OUTPUT_LIMIT.value
    default_message = "code execution output exceeded its limit"


class FileLimitError(ExecutionError):
    code = ExecutionErrorCode.FILE_LIMIT.value
    default_message = "code execution file limit was exceeded"


class PathTraversalError(ExecutionError):
    code = ExecutionErrorCode.PATH_TRAVERSAL.value
    default_message = "execution path is outside the allowed scope"


class NetworkDeniedError(ExecutionError):
    code = ExecutionErrorCode.NETWORK_DENIED.value
    default_message = "network access is disabled for this execution"


class ContainerFailureError(ExecutionError):
    code = ExecutionErrorCode.CONTAINER_FAILURE.value
    default_message = "container execution failed"


class CleanupFailureError(ExecutionError):
    code = ExecutionErrorCode.CLEANUP_FAILURE.value
    default_message = "execution cleanup failed"


__all__ = [
    "MAX_SAFE_TEXT_LENGTH",
    "BackendUnavailableError",
    "CleanupFailureError",
    "ConfigurationMissingError",
    "ContainerFailureError",
    "ExecutionError",
    "ExecutionErrorCode",
    "ExecutionTimeoutError",
    "FileLimitError",
    "NetworkDeniedError",
    "OutputLimitError",
    "PathTraversalError",
    "ResourceLimitError",
    "sanitize_execution_text",
    "truncate_execution_text",
]
