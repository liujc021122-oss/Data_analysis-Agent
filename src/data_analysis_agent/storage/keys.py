from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from .errors import StorageError, StorageErrorCode


_UNSAFE_FILENAME_CHARACTER = re.compile(r"[^\w.\-]+", re.UNICODE)
_DRIVE_PATH = re.compile(r"^[A-Za-z]:($|/)")


def _strip_control_characters(value: str) -> str:
    return "".join(
        character
        for character in value
        if unicodedata.category(character) not in {"Cc", "Cf"}
    )


def _has_control_characters(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    )


def normalize_filename(name: str, default_name: str = "file") -> str:
    normalized = unicodedata.normalize("NFKC", name)
    candidate = re.split(r"[\\/]", normalized)[-1]
    candidate = _strip_control_characters(candidate)
    candidate = re.sub(r"\s+", "_", candidate)
    candidate = _UNSAFE_FILENAME_CHARACTER.sub("_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")

    if not candidate:
        candidate = unicodedata.normalize("NFKC", default_name)
        candidate = _strip_control_characters(candidate)
        candidate = re.sub(r"\s+", "_", candidate)
        candidate = _UNSAFE_FILENAME_CHARACTER.sub("_", candidate)
        candidate = re.sub(r"_+", "_", candidate).strip("_")

    if not candidate or candidate in {".", ".."}:
        raise ValueError("filename must not normalize to an empty or parent name")

    return candidate


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise _invalid_key("key must be a non-empty string")

    normalized = unicodedata.normalize("NFKC", key)
    decoded = normalized
    for _ in range(3):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded

    for candidate in (key, normalized, decoded):
        if _has_control_characters(candidate):
            raise _invalid_key("control characters are not permitted")

    if "\\" in normalized or "\\" in decoded:
        raise _invalid_key("backslash path separators are not permitted")

    path = decoded
    if (
        path.startswith("/")
        or path.startswith("//")
        or _DRIVE_PATH.match(path)
        or PurePosixPath(path).is_absolute()
    ):
        raise _invalid_key("absolute paths are not permitted")
    parts = path.split("/")
    if any(part in {".", ".."} for part in parts):
        raise _invalid_key("relative traversal segments are not permitted")

    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise _invalid_key("URI syntax is not permitted in object keys")

    return key


def dataset_key(dataset_id: UUID) -> str:
    return validate_key(f"datasets/{dataset_id}/original.csv")


def task_file_key(
    task_id: UUID,
    file_id: UUID,
    kind: Literal["chart", "report"],
    filename: str,
) -> str:
    plural = "charts" if kind == "chart" else "reports"
    return validate_key(
        f"tasks/{task_id}/{plural}/{file_id}_{normalize_filename(filename)}"
    )


def _invalid_key(reason: str) -> StorageError:
    return StorageError(
        StorageErrorCode.INVALID_KEY,
        "object key violates the storage key policy",
        details={"reason": reason},
    )
