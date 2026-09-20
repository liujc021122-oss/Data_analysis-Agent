import hashlib
import importlib
from io import BytesIO
from uuid import UUID

import pytest

from data_analysis_agent.datasets import LocalStorageBackend
from data_analysis_agent.storage.errors import StorageError, StorageErrorCode
from data_analysis_agent.storage.keys import dataset_key, task_file_key
from data_analysis_agent.storage.models import StorageObject


class FailingReadStream:
    def __init__(self):
        self._reads = 0

    def read(self, _size=-1):
        self._reads += 1
        if self._reads == 1:
            return b"partial bytes"
        raise OSError("read failed")


def _local_module():
    try:
        return importlib.import_module("data_analysis_agent.storage.local")
    except ModuleNotFoundError:
        pytest.fail("LocalFileStorage adapter module has not been implemented", pytrace=False)


def _local_storage(tmp_path, **kwargs):
    return _local_module().LocalFileStorage(tmp_path, **kwargs)


def test_local_file_storage_put_get_stat_exists_and_idempotent_delete(tmp_path):
    storage = _local_storage(tmp_path, signing_secret=b"test-secret")
    payload = b"name,value\nAda,42\n"
    key = "datasets/11111111-1111-1111-1111-111111111111/original.csv"

    stored = storage.put(
        BytesIO(payload),
        key=key,
        content_type="text/csv; charset=utf-8",
        max_bytes=1024,
    )

    assert stored == StorageObject(
        uri=f"local://{key}",
        key=key,
        size_bytes=len(payload),
        checksum=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        content_type="text/csv; charset=utf-8",
    )
    assert storage.exists(stored.uri)
    assert storage.stat(stored.uri) == stored
    with storage.get(stored.uri) as stream:
        assert stream.read() == payload

    storage.delete(stored.uri)
    assert not storage.exists(stored.uri)
    storage.delete(stored.uri)

    with pytest.raises(StorageError) as exc_info:
        storage.get(stored.uri)
    assert exc_info.value.code is StorageErrorCode.OBJECT_NOT_FOUND


def test_stat_recomputes_current_size_and_checksum(tmp_path):
    storage = _local_storage(tmp_path)
    key = "datasets/current.csv"
    stored = storage.put(
        BytesIO(b"before"),
        key=key,
        content_type="application/octet-stream",
        max_bytes=100,
    )

    (tmp_path / "datasets" / "current.csv").write_bytes(b"after-change")

    current = storage.stat(stored.uri)
    assert current.size_bytes == len(b"after-change")
    assert current.checksum == (
        f"sha256:{hashlib.sha256(b'after-change').hexdigest()}"
    )
    assert current.content_type == "application/octet-stream"


@pytest.mark.parametrize(
    "key",
    [
        "/absolute.csv",
        r"C:\absolute.csv",
        "C:/absolute.csv",
        "../escape.csv",
        "datasets/./escape.csv",
        "datasets/../escape.csv",
        "datasets/%2e%2e/escape.csv",
        "datasets/%252e%252e/escape.csv",
        "datasets/object\x00.csv",
        "datasets/object\u0085.csv",
        "datasets/object\n.csv",
        "datasets/object?query=1",
        "datasets/object#fragment",
        "datasets/object%3f",
        "https://example.test/object.csv",
    ],
)
def test_put_rejects_unsafe_object_keys_without_exposing_root(tmp_path, key):
    storage = _local_storage(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.put(BytesIO(b"x"), key=key, content_type="text/plain", max_bytes=10)

    assert exc_info.value.code is StorageErrorCode.INVALID_KEY
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.parametrize(
    "uri",
    [
        "local://",
        "local:///absolute.csv",
        "local://../escape.csv",
        "local://datasets/%2e%2e/escape.csv",
        "local://C:/absolute.csv",
        "local://datasets/object?query=1",
        "local://datasets/object#fragment",
        "file:///outside.csv",
        "not-a-uri",
    ],
)
def test_exists_rejects_malformed_or_outside_root_references(tmp_path, uri):
    storage = _local_storage(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.exists(uri)

    assert exc_info.value.code in {
        StorageErrorCode.INVALID_URI,
        StorageErrorCode.INVALID_KEY,
    }
    assert str(tmp_path) not in str(exc_info.value)


def test_size_rejection_is_atomic_and_cleans_temporary_files(tmp_path):
    storage = _local_storage(tmp_path)
    key = "datasets/atomic.csv"
    destination = tmp_path / "datasets" / "atomic.csv"
    original = b"original destination"
    storage.put(
        BytesIO(original),
        key=key,
        content_type="text/plain",
        max_bytes=1024,
    )
    files_before = {path for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(StorageError) as exc_info:
        storage.put(
            BytesIO(b"too large"),
            key=key,
            content_type="text/plain",
            max_bytes=3,
        )

    assert exc_info.value.code is StorageErrorCode.FILE_TOO_LARGE
    assert destination.read_bytes() == original
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == files_before


def test_write_failure_is_atomic_and_cleans_temporary_files(tmp_path):
    storage = _local_storage(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.put(
            FailingReadStream(),
            key="tasks/22222222-2222-2222-2222-222222222222/charts/chart.png",
            content_type="image/png",
            max_bytes=1024,
        )

    assert exc_info.value.code is StorageErrorCode.BACKEND_UNAVAILABLE
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    assert str(tmp_path) not in str(exc_info.value)


def test_nested_dataset_and_task_keys_are_stored_below_root(tmp_path):
    storage = _local_storage(tmp_path)
    dataset_key_value = dataset_key(UUID("11111111-1111-1111-1111-111111111111"))
    task_key = task_file_key(
        UUID("22222222-2222-2222-2222-222222222222"),
        UUID("33333333-3333-3333-3333-333333333333"),
        "chart",
        "Monthly chart.png",
    )

    dataset_object = storage.put(
        BytesIO(b"dataset"),
        key=dataset_key_value,
        content_type="text/csv",
        max_bytes=100,
    )
    task_object = storage.put(
        BytesIO(b"chart"),
        key=task_key,
        content_type="image/png",
        max_bytes=100,
    )

    assert (tmp_path / dataset_key_value).is_file()
    assert (tmp_path / task_key).is_file()
    assert storage.exists(dataset_object.uri)
    assert storage.exists(task_object.uri)


def test_create_download_url_is_opaque_and_reads_until_expiry(tmp_path):
    storage = _local_storage(tmp_path, signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"download me"),
        key="tasks/22222222-2222-2222-2222-222222222222/reports/report.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        max_bytes=100,
    )

    download_url = storage.create_download_url(stored.uri, expires_in=300)

    assert download_url.startswith("local-download://")
    assert str(tmp_path) not in download_url
    with storage.get(download_url) as stream:
        assert stream.read() == b"download me"


def test_create_download_url_rejects_non_positive_expiry(tmp_path):
    storage = _local_storage(tmp_path, signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"download me"),
        key="datasets/download.csv",
        content_type="text/csv",
        max_bytes=100,
    )

    for expires_in in (0, -1):
        with pytest.raises(StorageError) as exc_info:
            storage.create_download_url(stored.uri, expires_in=expires_in)
        assert exc_info.value.code is StorageErrorCode.DOWNLOAD_URL_INVALID


def test_signed_download_rejects_malformed_and_tampered_values(tmp_path):
    storage = _local_storage(tmp_path, signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"download me"),
        key="datasets/download.csv",
        content_type="text/csv",
        max_bytes=100,
    )
    download_url = storage.create_download_url(stored.uri, expires_in=300)
    token = download_url.removeprefix("local-download://")
    payload, signature = token.split(".", 1)
    replacement = "A" if payload[-1] != "A" else "B"
    tampered = f"local-download://{payload[:-1]}{replacement}.{signature}"

    for invalid_url in (
        "local-download://",
        "local-download://not-a-token",
        "local-download://.",
        tampered,
    ):
        with pytest.raises(StorageError) as exc_info:
            storage.get(invalid_url)
        assert exc_info.value.code is StorageErrorCode.DOWNLOAD_URL_INVALID


def test_signed_download_rejects_expired_values(tmp_path, monkeypatch):
    now = [1_800_000_000]
    local_module = _local_module()
    monkeypatch.setattr(local_module.time, "time", lambda: now[0])
    storage = local_module.LocalFileStorage(tmp_path, signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"download me"),
        key="datasets/download.csv",
        content_type="text/csv",
        max_bytes=100,
    )
    download_url = storage.create_download_url(stored.uri, expires_in=1)
    now[0] += 2

    with pytest.raises(StorageError) as exc_info:
        storage.get(download_url)
    assert exc_info.value.code is StorageErrorCode.DOWNLOAD_URL_EXPIRED


def test_legacy_local_storage_backend_delegates_to_canonical_adapter(tmp_path):
    storage = LocalStorageBackend(tmp_path, signing_secret=b"test-secret")

    stored = storage.put_stream(
        BytesIO(b"name,value\nAda,42\n"),
        key="datasets/legacy.csv",
        max_bytes=1024,
    )

    assert isinstance(storage, _local_module().LocalFileStorage)
    assert isinstance(stored, StorageObject)
    assert stored.uri == "local://datasets/legacy.csv"
    assert stored.content_type == "text/csv"
    with storage.open(stored.uri) as stream:
        assert stream.read() == b"name,value\nAda,42\n"


def test_legacy_size_error_keeps_m04_error_code_identity(tmp_path):
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.put_stream(BytesIO(b"0123456789"), key="datasets/a.csv", max_bytes=5)

    from data_analysis_agent.datasets.errors import DatasetErrorCode

    assert exc_info.value.code is DatasetErrorCode.FILE_TOO_LARGE
