from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.storage import LocalFileStorage
from data_analysis_agent.storage.artifacts import ArtifactStorageService
from data_analysis_agent.storage.errors import StorageError, StorageErrorCode


class MemoryArtifactRepository:
    def __init__(self, *, error=None):
        self.records = {}
        self.error = error

    def add(self, record):
        if self.error is not None:
            raise self.error
        self.records[record.artifact_id] = record
        return record

    def get(self, artifact_id):
        return self.records.get(artifact_id)

    def list_for_task(self, task_id):
        return [record for record in self.records.values() if record.task_id == task_id]

    def get_for_user(self, artifact_id, user_id):
        return self.records.get(artifact_id)


class MemoryReportRepository:
    def __init__(self, *, error=None):
        self.records = {}
        self.error = error

    def add(self, record):
        if self.error is not None:
            raise self.error
        self.records[record.report_id] = record
        return record


class RecordingStorage:
    def __init__(self, root):
        self.delegate = LocalFileStorage(root)
        self.delete_calls = []

    def put(self, stream, *, key, content_type, max_bytes=None):
        return self.delegate.put(
            stream,
            key=key,
            content_type=content_type,
            max_bytes=max_bytes,
        )

    def get(self, uri):
        return self.delegate.get(uri)

    def stat(self, uri):
        return self.delegate.stat(uri)

    def exists(self, uri):
        return self.delegate.exists(uri)

    def delete(self, uri):
        self.delete_calls.append(uri)
        return self.delegate.delete(uri)

    def create_download_url(self, uri, *, expires_in=300):
        return self.delegate.create_download_url(uri, expires_in=expires_in)


def _write_source(tmp_path: Path, name: str, payload: bytes) -> Path:
    source = tmp_path / name
    source.write_bytes(payload)
    return source


def test_store_chart_uses_task_file_namespace_and_persists_opaque_uri(tmp_path):
    task_id = uuid4()
    source = _write_source(tmp_path, "chart.png", b"png-bytes")
    storage = LocalFileStorage(tmp_path / "objects")
    repository = MemoryArtifactRepository()
    service = ArtifactStorageService(storage=storage, artifact_repository=repository)

    record = service.store_file(
        task_id=task_id,
        source_path=source,
        artifact_type="CHART",
        filename="../trend.png",
        mime_type="image/png",
        title="Trend",
    )

    assert record.file_path == (
        f"local://tasks/{task_id}/charts/{record.artifact_id}_trend.png"
    )
    assert str(tmp_path) not in record.file_path
    assert record.size_bytes == len(b"png-bytes")
    assert record.content_hash.startswith("sha256:")
    with storage.get(record.file_path) as stored:
        assert stored.read() == b"png-bytes"
    assert repository.records[record.artifact_id] == record


def test_store_file_uses_safe_metadata_name_for_path_only_filename(tmp_path):
    source = _write_source(tmp_path, "chart.png", b"png-bytes")
    service = ArtifactStorageService(
        storage=LocalFileStorage(tmp_path / "objects"),
        artifact_repository=MemoryArtifactRepository(),
    )

    record = service.store_file(
        task_id=uuid4(),
        source_path=source,
        artifact_type="CHART",
        filename="../../",
        mime_type="image/png",
    )

    assert record.name == "file"
    assert record.file_path.endswith(f"_{record.name}")


def test_store_report_persists_artifact_and_report_metadata(tmp_path):
    task_id = uuid4()
    source = _write_source(tmp_path, "report.md", b"# Report\n")
    storage = LocalFileStorage(tmp_path / "objects")
    artifact_repository = MemoryArtifactRepository()
    report_repository = MemoryReportRepository()
    service = ArtifactStorageService(
        storage=storage,
        artifact_repository=artifact_repository,
        report_repository=report_repository,
    )

    artifact, report = service.store_report(
        task_id=task_id,
        source_path=source,
        filename="report.md",
        mime_type="text/markdown",
        format="MARKDOWN",
        title="Final report",
    )

    assert artifact.artifact_type == "REPORT"
    assert artifact.file_path == (
        f"local://tasks/{task_id}/reports/{artifact.artifact_id}_report.md"
    )
    assert artifact.format == "MARKDOWN"
    assert report.storage_uri == artifact.file_path
    assert report.size_bytes == artifact.size_bytes
    assert report.content_hash == artifact.content_hash
    assert report_repository.records[report.report_id] == report


def test_store_file_deletes_object_when_artifact_metadata_persistence_fails(tmp_path):
    source = _write_source(tmp_path, "chart.png", b"png-bytes")
    storage = RecordingStorage(tmp_path / "objects")
    repository = MemoryArtifactRepository(error=RuntimeError("database unavailable"))
    service = ArtifactStorageService(storage=storage, artifact_repository=repository)

    with pytest.raises(StorageError):
        service.store_file(
            task_id=uuid4(),
            source_path=source,
            artifact_type="CHART",
            filename="chart.png",
            mime_type="image/png",
        )

    assert len(storage.delete_calls) == 1
    assert not any(path.is_file() for path in (tmp_path / "objects").rglob("*"))


def test_store_report_deletes_object_when_report_metadata_persistence_fails(tmp_path):
    source = _write_source(tmp_path, "report.md", b"# Report\n")
    storage = RecordingStorage(tmp_path / "objects")
    artifact_repository = MemoryArtifactRepository()
    report_repository = MemoryReportRepository(error=RuntimeError("database unavailable"))
    service = ArtifactStorageService(
        storage=storage,
        artifact_repository=artifact_repository,
        report_repository=report_repository,
    )

    with pytest.raises(StorageError):
        service.store_report(
            task_id=uuid4(),
            source_path=source,
            filename="report.md",
            mime_type="text/markdown",
            format="MARKDOWN",
        )

    assert len(storage.delete_calls) == 1
    assert not any(path.is_file() for path in (tmp_path / "objects").rglob("*"))


def test_verify_detects_missing_and_modified_artifacts(tmp_path):
    task_id = uuid4()
    source = _write_source(tmp_path, "chart.png", b"before")
    storage = LocalFileStorage(tmp_path / "objects")
    service = ArtifactStorageService(
        storage=storage,
        artifact_repository=MemoryArtifactRepository(),
    )
    record = service.store_file(
        task_id=task_id,
        source_path=source,
        artifact_type="CHART",
        filename="chart.png",
        mime_type="image/png",
    )

    assert service.verify(record).checksum == record.content_hash
    storage.put(
        BytesIO(b"after"),
        key=f"tasks/{task_id}/charts/{record.artifact_id}_chart.png",
        content_type="image/png",
    )
    with pytest.raises(StorageError) as mismatch:
        service.verify(record)
    assert mismatch.value.code is StorageErrorCode.METADATA_MISMATCH
    storage.delete(record.file_path)
    with pytest.raises(StorageError) as missing:
        service.verify(record)
    assert missing.value.code is StorageErrorCode.OBJECT_NOT_FOUND


def test_delete_task_files_deduplicates_uris(tmp_path):
    task_id = uuid4()
    source = _write_source(tmp_path, "chart.png", b"png-bytes")
    storage = RecordingStorage(tmp_path / "objects")
    repository = MemoryArtifactRepository()
    service = ArtifactStorageService(storage=storage, artifact_repository=repository)
    first = service.store_file(
        task_id=task_id,
        source_path=source,
        artifact_type="CHART",
        filename="chart.png",
        mime_type="image/png",
    )
    duplicate = first.model_copy(update={"artifact_id": uuid4()})

    service.delete_task_files(task_id=task_id, records=[first, duplicate])

    assert storage.delete_calls == [first.file_path]
