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
    model_validator,
)

from data_analysis_agent.storage.models import StorageObject


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

    @model_validator(mode="after")
    def _validate_sensitive_preview_values(self):
        sensitive_columns = {
            field.column_name for field in self.sensitive_fields
        }
        for row_index, row in enumerate(self.preview_rows):
            for column_name in sensitive_columns:
                if column_name in row and row[column_name] != "[REDACTED]":
                    raise ValueError(
                        f"preview_rows[{row_index}][{column_name!r}] "
                        "must be exactly [REDACTED]"
                    )
        return self


class DatasetUploadResult(DatasetModel):
    dataset_id: UUID
    original_filename: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(ge=0)
    checksum: StrictStr
    profile: DatasetProfile


StoredObject = StorageObject
