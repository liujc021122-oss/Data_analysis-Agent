from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.datasets.errors import (
    DatasetAccessDeniedError,
    DatasetErrorCode,
    DatasetPersistenceError,
)
from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.resolver import DatasetResolver
from data_analysis_agent.datasets.service import (
    DatasetUploadService,
    UnitOfWorkDatasetStore,
)
from data_analysis_agent.datasets.storage import LocalStorageBackend
from data_analysis_agent.storage import dataset_key


class CanonicalRecordingStorage:
    """A provider-neutral fake: it deliberately has no M04 adapter methods."""

    def __init__(self, root: Path):
        self._delegate = LocalStorageBackend(root)
        self.put_calls = []
        self.get_calls = []
        self.exists_calls = []
        self.stat_calls = []
        self.delete_calls = []
        self.events = []

    def put(self, stream, *, key, content_type, max_bytes=None):
        self.put_calls.append(
            {
                "key": key,
                "content_type": content_type,
                "max_bytes": max_bytes,
            }
        )
        self.events.append("put")
        return self._delegate.put(
            stream,
            key=key,
            content_type=content_type,
            max_bytes=max_bytes,
        )

    def get(self, uri):
        self.get_calls.append(uri)
        self.events.append("get")
        return self._delegate.get(uri)

    def exists(self, uri):
        self.exists_calls.append(uri)
        self.events.append("exists")
        return self._delegate.exists(uri)

    def stat(self, uri):
        self.stat_calls.append(uri)
        self.events.append("stat")
        return self._delegate.stat(uri)

    def delete(self, uri):
        self.delete_calls.append(uri)
        self.events.append("delete")
        return self._delegate.delete(uri)

    def create_download_url(self, uri, *, expires_in=300):
        return self._delegate.create_download_url(uri, expires_in=expires_in)


class FailingMetadataStore:
    def __init__(self, error):
        self.error = error

    def create(self, record):
        raise self.error

    def get_for_user(self, dataset_id, user_id):
        return None


def _upload_service(storage, uow_factory):
    return DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )


def test_upload_uses_canonical_put_and_task_scoped_dataset_key(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = CanonicalRecordingStorage(tmp_path / "objects")

    result = _upload_service(storage, uow_factory).upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="../../sales.csv",
        owner_id=owner_id,
    )

    assert storage.put_calls == [
        {
            "key": dataset_key(result.dataset_id),
            "content_type": "text/csv",
            "max_bytes": 1024,
        }
    ]
    assert result.original_filename == "sales.csv"
    assert storage.put_calls[0]["key"] == (
        f"datasets/{result.dataset_id}/original.csv"
    )

    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)

    assert record is not None
    assert record.source_uri == f"local://datasets/{result.dataset_id}/original.csv"
    assert str(tmp_path) not in record.source_uri
    assert record.name == "sales.csv"
    assert record.metadata_json["original_filename"] == "sales.csv"


def test_resolver_checks_owner_before_storage_existence(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    service = _upload_service(storage, uow_factory)
    result = service.upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="sales.csv",
        owner_id=owner_id,
    )
    resolver = DatasetResolver(
        storage=storage,
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
    )

    with pytest.raises(DatasetAccessDeniedError) as exc_info:
        resolver.open_for_user(result.dataset_id, owner_id=uuid4())

    assert exc_info.value.code is DatasetErrorCode.DATASET_ACCESS_DENIED


def test_resolver_verifies_storage_before_returning_stream(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = CanonicalRecordingStorage(tmp_path / "objects")
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )
    result = service.upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="sales.csv",
        owner_id=owner_id,
    )
    storage.events.clear()
    resolver = DatasetResolver(
        storage=storage,
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
    )

    with resolver.open_for_user(result.dataset_id, owner_id=owner_id) as stream:
        assert stream.read() == b"name,value\nA,1\n"

    assert storage.events == ["exists", "stat", "get"]


def test_resolver_reports_missing_object_as_storage_consistency_error(
    tmp_path, uow_factory
):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    service = _upload_service(storage, uow_factory)
    result = service.upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="sales.csv",
        owner_id=owner_id,
    )
    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)
    storage.delete(record.source_uri)
    resolver = DatasetResolver(
        storage=storage,
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
    )

    with pytest.raises(DatasetPersistenceError) as exc_info:
        resolver.open_for_user(result.dataset_id, owner_id=owner_id)

    assert exc_info.value.code is DatasetErrorCode.STORAGE_OBJECT_NOT_FOUND
    assert record.source_uri not in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_resolver_reports_modified_object_metadata_mismatch(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    service = _upload_service(storage, uow_factory)
    result = service.upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="sales.csv",
        owner_id=owner_id,
    )
    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)

    key = record.source_uri.removeprefix("local://")
    storage.put(
        BytesIO(b"name,value\nA,999999\n"),
        key=key,
        content_type="text/csv",
    )
    resolver = DatasetResolver(
        storage=storage,
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
    )

    with pytest.raises(DatasetPersistenceError) as exc_info:
        resolver.open_for_user(result.dataset_id, owner_id=owner_id)

    assert exc_info.value.code is DatasetErrorCode.STORAGE_METADATA_MISMATCH
    assert record.source_uri not in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_metadata_failure_cleans_object_and_local_sidecar(tmp_path):
    storage = CanonicalRecordingStorage(tmp_path / "objects")
    cause = RuntimeError("metadata store unavailable")
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=FailingMetadataStore(cause),
        max_upload_size=1024,
    )

    with pytest.raises(DatasetPersistenceError) as exc_info:
        service.upload(
            BytesIO(b"name,value\nA,1\n"),
            original_filename="sales.csv",
            owner_id=uuid4(),
        )

    assert exc_info.value.__cause__ is cause
    assert storage.delete_calls
    object_files = [
        path
        for path in (tmp_path / "objects").rglob("*")
        if path.is_file()
    ]
    assert object_files == []
