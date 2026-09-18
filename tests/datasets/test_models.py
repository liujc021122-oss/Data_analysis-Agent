from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.datasets.errors import DatasetErrorCode, UploadValidationError
from data_analysis_agent.datasets.models import (
    ColumnProfile,
    DatasetProfile,
    DatasetUploadResult,
    SensitiveField,
)


def test_profile_serializes_to_json_with_at_most_twenty_preview_rows():
    profile = DatasetProfile(
        encoding="utf-8",
        delimiter=",",
        row_count=1,
        column_count=1,
        columns=(
            ColumnProfile(
                name="email",
                inferred_type="string",
                non_null_count=1,
                missing_count=0,
                missing_rate=0.0,
            ),
        ),
        preview_rows=({"email": "[REDACTED]"},),
        sensitive_fields=(
            SensitiveField(
                column_name="email",
                risk_type="email",
                detected_by=("name", "value"),
            ),
        ),
    )

    payload = profile.model_dump_json()

    assert '"encoding":"utf-8"' in payload
    assert "[REDACTED]" in payload
    assert "email@example.com" not in payload


def test_profile_rejects_raw_value_for_declared_sensitive_preview_column():
    with pytest.raises(ValidationError, match=r"must be exactly \[REDACTED\]"):
        DatasetProfile(
            encoding="utf-8",
            delimiter=",",
            row_count=1,
            column_count=1,
            columns=(),
            preview_rows=({"email": "person@example.com"},),
            sensitive_fields=(
                SensitiveField(
                    column_name="email",
                    risk_type="email",
                    detected_by=("name",),
                ),
            ),
        )


def test_profile_rejects_negative_counts_and_unknown_fields():
    with pytest.raises(ValidationError, match="missing_count"):
        ColumnProfile(
            name="value",
            inferred_type="number",
            non_null_count=1,
            missing_count=-1,
            missing_rate=0.0,
        )

    with pytest.raises(ValidationError, match="extra"):
        DatasetProfile(
            encoding="utf-8",
            delimiter=",",
            row_count=0,
            column_count=1,
            columns=(),
            preview_rows=(),
            sensitive_fields=(),
            unexpected=True,
        )


def test_validation_error_exposes_stable_code_without_echoing_path():
    error = UploadValidationError(
        DatasetErrorCode.UNSUPPORTED_EXTENSION,
        "only CSV files are supported",
        details={"filename": "report.csv"},
    )

    assert error.code is DatasetErrorCode.UNSUPPORTED_EXTENSION
    assert "report.csv" in error.details["filename"]
    assert "C:\\secret" not in str(error)
