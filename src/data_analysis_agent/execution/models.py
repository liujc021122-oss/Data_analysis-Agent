from __future__ import annotations

from enum import Enum
from hashlib import sha256
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from .errors import (
    ExecutionErrorCode,
    MAX_SAFE_TEXT_LENGTH,
    sanitize_execution_text,
)


MAX_TIMEOUT_SECONDS = 3_600.0
MAX_MEMORY_LIMIT_BYTES = 8 * 1024**3
MAX_CPU_LIMIT = 64.0
MAX_PIDS_LIMIT = 4_096
MAX_OUTPUT_BYTES = 64 * 1024**2
MAX_FILES = 1_000

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MEMORY_LIMIT_BYTES = 512 * 1024**2
DEFAULT_CPU_LIMIT = 1.0
DEFAULT_PIDS_LIMIT = 64
DEFAULT_MAX_OUTPUT_BYTES = 1 * 1024**2
DEFAULT_MAX_FILES = 100


class NetworkPolicy(str, Enum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class ExecutionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        validate_default=True,
        populate_by_name=True,
    )


def _resolve_path(value: Path) -> Path:
    return value.expanduser().resolve(strict=False)


def _is_within(path: Path, scope: Path) -> bool:
    try:
        path.relative_to(scope)
    except ValueError:
        return False
    return True


def _path_error(message: str) -> ValueError:
    return ValueError(f"{ExecutionErrorCode.PATH_TRAVERSAL.value}: {message}")


def _validate_logical_name(value: str) -> str:
    name = value.strip()
    if not name or "\x00" in name:
        raise _path_error("logical file name is unsafe")
    if name.startswith(("/", "\\")):
        raise _path_error("logical file name is unsafe")
    if PureWindowsPath(name).drive or PureWindowsPath(name).root:
        raise _path_error("logical file name is unsafe")
    if ":" in name or "\\" in name:
        raise _path_error("logical file name is unsafe")
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _path_error("logical file name is unsafe")
    return name


def _validate_sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    return value.lower()


def code_sha256(code: str) -> str:
    """Return the deterministic UTF-8 SHA-256 digest for source code."""
    return sha256(code.encode("utf-8")).hexdigest()


class ExecutionLimits(ExecutionModel):
    timeout_seconds: StrictFloat = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_TIMEOUT_SECONDS,
    )
    memory_limit_bytes: StrictInt = Field(
        default=DEFAULT_MEMORY_LIMIT_BYTES,
        gt=0,
        le=MAX_MEMORY_LIMIT_BYTES,
    )
    cpu_limit: StrictFloat = Field(default=DEFAULT_CPU_LIMIT, gt=0, le=MAX_CPU_LIMIT)
    pids_limit: StrictInt = Field(default=DEFAULT_PIDS_LIMIT, gt=0, le=MAX_PIDS_LIMIT)
    max_output_bytes: StrictInt = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES,
        gt=0,
        le=MAX_OUTPUT_BYTES,
    )
    max_files: StrictInt = Field(default=DEFAULT_MAX_FILES, gt=0, le=MAX_FILES)

    @field_validator("timeout_seconds", "cpu_limit")
    @classmethod
    def _finite_float(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class ExecutionInput(ExecutionModel):
    logical_name: StrictStr
    source_path: Path
    source_scope: Path = Field(
        validation_alias=AliasChoices(
            "source_scope",
            "allowed_source_dir",
            "source_root",
        )
    )
    read_only: Literal[True] = True

    _logical_name = field_validator("logical_name")(_validate_logical_name)

    @model_validator(mode="after")
    def _validate_source_scope(self) -> "ExecutionInput":
        scope = _resolve_path(self.source_scope)
        source = _resolve_path(self.source_path)
        if not _is_within(source, scope):
            raise _path_error("input path is outside the allowed source scope")
        object.__setattr__(self, "source_scope", scope)
        object.__setattr__(self, "source_path", source)
        return self

    @property
    def resolved_source_path(self) -> Path:
        return self.source_path

    @field_serializer("source_path", "source_scope", when_used="json")
    def _serialize_path(self, value: Path) -> str:
        return str(value)


class ExecutionRequest(ExecutionModel):
    task_id: UUID
    code: StrictStr
    input_files: tuple[ExecutionInput, ...] = ()
    output_dir: Path
    output_scope: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "output_scope",
            "allowed_output_dir",
            "output_root",
        ),
    )
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    network_policy: NetworkPolicy = NetworkPolicy.DISABLED

    @model_validator(mode="after")
    def _validate_output_scope_and_inputs(self) -> "ExecutionRequest":
        output_dir = _resolve_path(self.output_dir)
        output_scope = (
            _resolve_path(self.output_scope) if self.output_scope is not None else None
        )
        if output_scope is not None and not _is_within(output_dir, output_scope):
            raise _path_error("output directory is outside the allowed scope")
        logical_names = [item.logical_name for item in self.input_files]
        if len(logical_names) != len(set(logical_names)):
            raise ValueError("input logical file names must be unique")
        object.__setattr__(self, "output_dir", output_dir)
        object.__setattr__(self, "output_scope", output_scope)
        return self

    @property
    def code_sha256(self) -> str:
        return code_sha256(self.code)

    @field_serializer("output_dir", "output_scope", when_used="json")
    def _serialize_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None


class ExecutionFile(ExecutionModel):
    logical_name: StrictStr
    size_bytes: StrictInt = Field(
        validation_alias=AliasChoices("size_bytes", "size"),
        ge=0,
    )
    sha256: StrictStr = Field(
        validation_alias=AliasChoices("sha256", "sha256_hex", "content_hash")
    )
    mime_type: StrictStr | None = Field(default=None, min_length=1)
    suffix: StrictStr | None = Field(default=None, min_length=1)

    _logical_name = field_validator("logical_name")(_validate_logical_name)
    _sha256 = field_validator("sha256")(_validate_sha256)

    @model_validator(mode="after")
    def _derive_suffix(self) -> "ExecutionFile":
        if self.suffix is None:
            object.__setattr__(
                self,
                "suffix",
                PurePosixPath(self.logical_name).suffix.lower() or None,
            )
        return self


class ExecutionResult(ExecutionModel):
    success: StrictBool
    stdout: StrictStr = ""
    stderr: StrictStr = ""
    exit_code: StrictInt | None = None
    timed_out: StrictBool = Field(
        default=False,
        validation_alias=AliasChoices("timed_out", "timeout"),
    )
    resource_limited: StrictBool = Field(
        default=False,
        validation_alias=AliasChoices("resource_limited", "resource_limit_hit"),
    )
    error_code: ExecutionErrorCode | None = None
    error_message: StrictStr | None = Field(
        default=None,
        validation_alias=AliasChoices("error_message", "error"),
    )
    code_sha256: StrictStr
    duration_ms: float = Field(ge=0)
    output_files: tuple[ExecutionFile, ...] = Field(
        default=(),
        validation_alias=AliasChoices("output_files", "files"),
    )

    _stdout = field_validator("stdout", "stderr", mode="before")(
        lambda value: sanitize_execution_text(value)
    )
    _error_message = field_validator("error_message", mode="before")(
        lambda value: None
        if value is None
        else sanitize_execution_text(value)
    )
    _code_sha256 = field_validator("code_sha256")(_validate_sha256)

    @field_validator("duration_ms")
    @classmethod
    def _finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("duration_ms must be finite")
        return value

    @model_validator(mode="after")
    def _fill_stable_limit_code(self) -> "ExecutionResult":
        if self.error_code is None:
            if self.timed_out:
                object.__setattr__(self, "error_code", ExecutionErrorCode.TIMEOUT)
            elif self.resource_limited:
                object.__setattr__(self, "error_code", ExecutionErrorCode.RESOURCE_LIMIT)
        return self


__all__ = [
    "DEFAULT_CPU_LIMIT",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MEMORY_LIMIT_BYTES",
    "DEFAULT_PIDS_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
    "ExecutionFile",
    "ExecutionInput",
    "ExecutionLimits",
    "ExecutionModel",
    "ExecutionRequest",
    "ExecutionResult",
    "MAX_CPU_LIMIT",
    "MAX_FILES",
    "MAX_MEMORY_LIMIT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_PIDS_LIMIT",
    "MAX_SAFE_TEXT_LENGTH",
    "MAX_TIMEOUT_SECONDS",
    "NetworkPolicy",
    "code_sha256",
]
