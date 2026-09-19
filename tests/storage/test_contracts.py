from dataclasses import asdict
import inspect
import json
from uuid import UUID

import pytest

from data_analysis_agent.datasets import StoredObject as ExportedStoredObject
from data_analysis_agent.datasets.models import StoredObject
from data_analysis_agent.storage import Storage, StorageObject
from data_analysis_agent.storage.errors import StorageError, StorageErrorCode
from data_analysis_agent.storage.keys import (
    dataset_key,
    normalize_filename,
    task_file_key,
    validate_key,
)


def test_storage_protocol_exposes_contract_methods_and_arguments():
    assert {
        "put",
        "get",
        "delete",
        "exists",
        "stat",
        "create_download_url",
    }.issubset(Storage.__dict__)

    put_parameters = inspect.signature(Storage.put).parameters
    assert list(put_parameters) == [
        "self",
        "stream",
        "key",
        "content_type",
        "max_bytes",
    ]
    assert put_parameters["key"].kind is inspect.Parameter.KEYWORD_ONLY
    assert put_parameters["content_type"].kind is inspect.Parameter.KEYWORD_ONLY
    assert put_parameters["max_bytes"].default is None


def test_storage_object_is_json_safe_and_has_metadata_fields():
    stored = StorageObject(
        uri="s3://bucket/object.csv",
        key="datasets/123/original.csv",
        size_bytes=12,
        checksum="sha256:abc",
        content_type="text/csv",
    )

    payload = json.loads(json.dumps(asdict(stored)))

    assert payload == {
        "uri": "s3://bucket/object.csv",
        "size_bytes": 12,
        "checksum": "sha256:abc",
        "content_type": "text/csv",
        "key": "datasets/123/original.csv",
    }


def test_stored_object_compatibility_alias_preserves_old_constructor_and_adds_key():
    legacy = StoredObject("local://datasets/123/original.csv", 12, "sha256:abc")

    assert StoredObject is StorageObject
    assert ExportedStoredObject is StorageObject
    assert legacy.key == ""
    assert legacy.content_type == "text/csv"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (r"C:\uploads\My report 2025.csv", "My_report_2025.csv"),
        ("résumé\u00a02025.csv", "résumé_2025.csv"),
        ("raw\x00name\n.csv", "rawname.csv"),
        ("   ", "file"),
        ("archive.tar.gz", "archive.tar.gz"),
    ],
)
def test_normalize_filename_strips_components_controls_and_preserves_extension(
    name, expected
):
    assert normalize_filename(name) == expected


@pytest.mark.parametrize("name", [".", "..", "foo/.", r"foo\.."])
def test_normalize_filename_rejects_empty_and_parent_results(name):
    with pytest.raises(ValueError):
        normalize_filename(name)


def test_dataset_key_uses_dataset_id_layout():
    dataset_id = UUID("11111111-1111-1111-1111-111111111111")

    assert dataset_key(dataset_id) == (
        "datasets/11111111-1111-1111-1111-111111111111/original.csv"
    )


def test_task_file_key_uses_plural_kind_and_normalized_filename():
    task_id = UUID("22222222-2222-2222-2222-222222222222")
    file_id = UUID("33333333-3333-3333-3333-333333333333")

    assert task_file_key(task_id, file_id, "chart", "Monthly chart.png") == (
        "tasks/22222222-2222-2222-2222-222222222222/charts/"
        "33333333-3333-3333-3333-333333333333_Monthly_chart.png"
    )
    assert task_file_key(task_id, file_id, "report", "final report.docx") == (
        "tasks/22222222-2222-2222-2222-222222222222/reports/"
        "33333333-3333-3333-3333-333333333333_final_report.docx"
    )


@pytest.mark.parametrize(
    "key",
    [
        "/absolute.csv",
        r"C:\absolute.csv",
        "C:/absolute.csv",
        "../escape.csv",
        "datasets/./escape.csv",
        "datasets/../escape.csv",
        r"datasets\..\escape.csv",
        "datasets/%2e%2e/escape.csv",
        "datasets/%252e%252e/escape.csv",
        "datasets/object\x00.csv",
        "datasets/object\u0085.csv",
        "datasets/object\n.csv",
        "https://example.test/object.csv",
    ],
)
def test_validate_key_rejects_absolute_parent_uri_traversal_and_controls(key):
    with pytest.raises(StorageError) as exc_info:
        validate_key(key)

    assert exc_info.value.code is StorageErrorCode.INVALID_KEY
    assert "C:\\absolute.csv" not in str(exc_info.value)


def test_storage_error_codes_are_stable_and_details_are_structured():
    assert {code.value for code in StorageErrorCode} == {
        "INVALID_KEY",
        "INVALID_URI",
        "OBJECT_NOT_FOUND",
        "DOWNLOAD_URL_INVALID",
        "DOWNLOAD_URL_EXPIRED",
        "METADATA_MISMATCH",
        "BACKEND_UNAVAILABLE",
        "FILE_TOO_LARGE",
    }

    error = StorageError(
        StorageErrorCode.METADATA_MISMATCH,
        "stored metadata does not match expected metadata",
        details={"field": "checksum"},
    )

    assert error.code is StorageErrorCode.METADATA_MISMATCH
    assert error.message == "stored metadata does not match expected metadata"
    assert error.details == {"field": "checksum"}
