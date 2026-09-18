from io import BytesIO

import pytest

from data_analysis_agent.datasets.errors import DatasetErrorCode, StorageError
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_local_backend_writes_reads_and_deletes_only_generated_relative_keys(tmp_path):
    storage = LocalStorageBackend(tmp_path)

    stored = storage.put_stream(
        BytesIO(b"name,value\nA,1\n"),
        key="datasets/11111111-1111-1111-1111-111111111111.csv",
        max_bytes=1024,
    )

    assert stored.uri == "local://datasets/11111111-1111-1111-1111-111111111111.csv"
    assert storage.open(stored.uri).read() == b"name,value\nA,1\n"
    storage.delete(stored.uri)
    with pytest.raises(StorageError):
        storage.open(stored.uri)


@pytest.mark.parametrize("key", ["../escape.csv", "/absolute.csv", "datasets/../escape.csv"])
def test_local_backend_rejects_path_traversal_keys(tmp_path, key):
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(StorageError, match="path"):
        storage.put_stream(BytesIO(b"x\n1\n"), key=key, max_bytes=1024)


def test_local_backend_removes_partial_file_when_stream_exceeds_limit(tmp_path):
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.put_stream(BytesIO(b"0123456789"), key="datasets/a.csv", max_bytes=5)

    assert exc_info.value.code is DatasetErrorCode.FILE_TOO_LARGE
    assert not (tmp_path / "datasets" / "a.csv").exists()
