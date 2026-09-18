"""Strict, lightweight inspection of uploaded CSV datasets."""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from io import TextIOWrapper
from typing import BinaryIO

from charset_normalizer import from_bytes

from .errors import DatasetErrorCode, UploadValidationError
from .models import ColumnProfile, DatasetProfile, SensitiveField


_SUPPORTED_DELIMITERS = ",;\t|"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MOBILE = re.compile(r"^1[3-9]\d{9}$")
_CHINESE_ID = re.compile(r"^(?:\d{15}|\d{17}[\dXx])$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_NUMBER = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def _validation(code: DatasetErrorCode, message: str) -> UploadValidationError:
    return UploadValidationError(code, message)


def _decode(payload: bytes) -> tuple[str, str]:
    if payload.startswith(b"\xef\xbb\xbf"):
        try:
            return payload.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass

    try:
        match = from_bytes(payload).best()
        if match is not None and match.encoding:
            encoding = match.encoding.lower()
            if encoding in {"cp949", "euc-kr", "shift_jis", "cp932"}:
                try:
                    return payload.decode("gb18030"), "gb18030"
                except UnicodeDecodeError:
                    pass
            return str(match).replace("\r\n", "\n"), match.encoding
    except (UnicodeError, LookupError):
        pass

    for encoding in ("gb18030", "gbk"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise _validation(DatasetErrorCode.ENCODING_DETECTION_FAILED, "unable to decode CSV data")


def _value_type(value: str) -> str:
    value = value.strip()
    if not value:
        return "empty"
    if value.lower() in {"true", "false", "yes", "no"}:
        return "boolean"
    if _INTEGER.fullmatch(value):
        return "integer"
    if _NUMBER.fullmatch(value):
        return "number"
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        try:
            date.fromisoformat(value)
            return "datetime"
        except ValueError:
            return "string"


def _infer(values: list[str]) -> str:
    types = {_value_type(value) for value in values if value.strip()}
    if not types:
        return "empty"
    if len(types) == 1:
        return types.pop()
    return "mixed"


def _risk_for_value(value: str) -> str | None:
    value = value.strip()
    if _EMAIL.fullmatch(value):
        return "email"
    if _MOBILE.fullmatch(value):
        return "phone"
    if _CHINESE_ID.fullmatch(value):
        return "id_card"
    return None


def _risk_for_name(name: str) -> str | None:
    lowered = name.casefold()
    if "email" in lowered or "邮箱" in name:
        return "email"
    if any(token in lowered for token in ("phone", "mobile", "tel")) or any(
        token in name for token in ("手机", "电话")
    ):
        return "phone"
    if any(token in lowered for token in ("id card", "identity", "ssn")) or any(
        token in name for token in ("身份证", "身份")
    ):
        return "id_card"
    return None


class CsvInspector:
    """Validate a CSV stream and return a JSON-safe structural profile."""

    def inspect(self, stream: BinaryIO, *, filename: str) -> DatasetProfile:
        if not filename.lower().endswith(".csv"):
            raise _validation(DatasetErrorCode.UNSUPPORTED_EXTENSION, "only CSV files are supported")
        payload = stream.read()
        if not payload:
            raise _validation(DatasetErrorCode.EMPTY_FILE, "CSV file is empty")

        text, encoding = _decode(payload)
        if _CONTROL_CHARACTERS.search(text):
            raise _validation(DatasetErrorCode.CONTROL_CHARACTER, "CSV contains control characters")
        try:
            dialect = csv.Sniffer().sniff(text, delimiters=_SUPPORTED_DELIMITERS)
            delimiter = dialect.delimiter
            rows = list(csv.reader(text.splitlines(), dialect, strict=True))
        except (csv.Error, UnicodeError) as exc:
            raise _validation(DatasetErrorCode.INVALID_CSV, "invalid CSV structure") from exc
        if not rows or not rows[0]:
            raise _validation(DatasetErrorCode.EMPTY_FILE, "CSV file is empty")

        headers = [header.strip() for header in rows[0]]
        if any(not header for header in headers):
            raise _validation(DatasetErrorCode.EMPTY_COLUMN_NAME, "column names must not be empty")
        if len(set(headers)) != len(headers):
            raise _validation(DatasetErrorCode.DUPLICATE_COLUMNS, "column names must be unique")
        width = len(headers)
        if any(len(row) != width for row in rows[1:]):
            raise _validation(DatasetErrorCode.INVALID_CSV, "CSV rows must have consistent widths")

        data = [[cell.strip() for cell in row] for row in rows[1:]]
        sensitive: dict[str, tuple[str, set[str]]] = {}
        for index, name in enumerate(headers):
            name_risk = _risk_for_name(name)
            value_risk = next((risk for row in data if (risk := _risk_for_value(row[index]))), None)
            risk = name_risk or value_risk
            if risk:
                detected: set[str] = set()
                if name_risk:
                    detected.add("name")
                if value_risk:
                    detected.add("value")
                sensitive[name] = (risk, detected)

        columns = []
        for index, name in enumerate(headers):
            values = [row[index] for row in data]
            missing = sum(not value for value in values)
            columns.append(
                ColumnProfile(
                    name=name,
                    inferred_type=_infer(values),
                    non_null_count=len(values) - missing,
                    missing_count=missing,
                    missing_rate=(missing / len(values)) if values else 0.0,
                )
            )

        preview_rows = []
        for row in data[:20]:
            preview_rows.append({
                name: ("[REDACTED]" if name in sensitive else row[index])
                for index, name in enumerate(headers)
            })
        sensitive_fields = tuple(
            SensitiveField(column_name=name, risk_type=risk, detected_by=tuple(sorted(detected)))
            for name, (risk, detected) in sensitive.items()
        )
        return DatasetProfile(
            encoding=encoding,
            delimiter=delimiter,
            row_count=len(data),
            column_count=width,
            columns=tuple(columns),
            preview_rows=tuple(preview_rows),
            sensitive_fields=sensitive_fields,
        )
