from dataclasses import dataclass
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)


class DatasetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnProfile(DatasetModel):
    name: StrictStr
    inferred_type: Literal[
        "empty", "boolean", "integer", "number", "datetime", "string", "mixed"
    ]
    non_null_count: StrictInt = Field(ge=0)
    missing_count: StrictInt = Field(ge=0)
    missing_rate: StrictFloat = Field(ge=0.0, le=1.0)


class SensitiveField(DatasetModel):
    column_name: StrictStr
    risk_type: StrictStr
    detected_by: tuple[Literal["name", "value"], ...]


class DatasetProfile(DatasetModel):
    encoding: StrictStr
    delimiter: StrictStr
    row_count: StrictInt = Field(ge=0)
    column_count: StrictInt = Field(gt=0)
    columns: tuple[ColumnProfile, ...]
    preview_rows: tuple[dict[str, Any], ...] = Field(default=(), max_length=20)
    sensitive_fields: tuple[SensitiveField, ...] = ()

    @field_validator("preview_rows")
    @classmethod
    def _validate_json_safe(cls, value: tuple[dict[str, Any], ...]):
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("preview_rows must contain JSON-safe values") from exc
        return value


class DatasetUploadResult(DatasetModel):
    dataset_id: UUID
    original_filename: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(ge=0)
    checksum: StrictStr
    profile: DatasetProfile


@dataclass(frozen=True, slots=True)
class StoredObject:
    uri: str
    size_bytes: int
    checksum: str
    content_type: str = "text/csv"
