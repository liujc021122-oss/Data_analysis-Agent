from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.storage import LocalFileStorage
from data_analysis_agent.storage.artifacts import ArtifactStorageService
from data_analysis_agent.storage.errors import StorageError, StorageErrorCode
from data_analysis_agent.storage.lifecycle import StorageLifecycleService


class MemoryArtifactRepository:
    def add(self, record):
        return record


class FailingDeleteStorage:
    def __init__(self, delegate, failing_uri):
        self.delegate = delegate
        self.failing_uri = failing_uri
        self.delete_calls = []

    def put(self, *args, **kwargs):
        return self.delegate.put(*args, **kwargs)

    def exists(self, uri):
        return self.delegate.exists(uri)

    def stat(self, uri):
        return self.delegate.stat(uri)

    def delete(self, uri):
        self.delete_calls.append(uri)
        if uri == self.failing_uri:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "never-log-this-storage-failure",
            )
        return self.delegate.delete(uri)


def _record(task_id, uri, *, created_at=None):
    return ArtifactRecord(
        artifact_id=uuid4(),
        task_id=task_id,
        artifact_type="CHART",
        name="chart.png",
        file_path=uri,
        size_bytes=1,
        content_hash="sha256:x",
        created_at=created_at or datetime.now(timezone.utc),
    )


def _service(tmp_path, *, retention_days=30, now=None):
    storage = LocalFileStorage(tmp_path / "objects", signing_secret=b"test-secret")
    artifact_storage = ArtifactStorageService(
        storage=storage,
        artifact_repository=MemoryArtifactRepository(),
    )
    lifecycle = StorageLifecycleService(
        storage=storage,
        artifact_storage=artifact_storage,
        output_root=tmp_path / "outputs",
        retention_days=retention_days,
        clock=now,
    )
    return storage, lifecycle


def test_cleanup_continues_after_one_object_failure_and_removes_staging(tmp_path, caplog):
    task_id = uuid4()
    base_storage = LocalFileStorage(tmp_path / "objects")
    first = base_storage.put(
        BytesIO(b"first"),
        key=f"tasks/{task_id}/charts/{uuid4()}_first.png",
        content_type="image/png",
    )
    second = base_storage.put(
        BytesIO(b"second"),
        key=f"tasks/{task_id}/charts/{uuid4()}_second.png",
        content_type="image/png",
    )
    storage = FailingDeleteStorage(base_storage, first.uri)
    lifecycle = StorageLifecycleService(
        storage=storage,
        output_root=tmp_path / "outputs",
    )
    staging = tmp_path / "outputs" / "failed"
    staging.mkdir(parents=True)

    with pytest.raises(StorageError) as exc_info:
        lifecycle.cleanup_failed_task(
            task_id=task_id,
            staging_dir=staging,
            records=[_record(task_id, first.uri), _record(task_id, second.uri)],
        )

    assert exc_info.value.code is StorageErrorCode.BACKEND_UNAVAILABLE
    assert storage.delete_calls == [first.uri, second.uri]
    assert storage.exists(first.uri)
    assert not storage.exists(second.uri)
    assert not staging.exists()
    assert "never-log-this-storage-failure" not in caplog.text


def test_cleanup_failed_task_removes_only_task_objects_and_in_root_staging(tmp_path):
    task_id = uuid4()
    other_task_id = uuid4()
    storage, lifecycle = _service(tmp_path)
    task_object = storage.put(
        __import__("io").BytesIO(b"task"),
        key=f"tasks/{task_id}/charts/{uuid4()}_task.png",
        content_type="image/png",
    )
    other_object = storage.put(
        __import__("io").BytesIO(b"other"),
        key=f"tasks/{other_task_id}/charts/{uuid4()}_other.png",
        content_type="image/png",
    )
    staging = tmp_path / "outputs" / "session_failed"
    staging.mkdir(parents=True)
    (staging / "partial.txt").write_text("partial", encoding="utf-8")
    unrelated = tmp_path / "outputs" / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    lifecycle.cleanup_failed_task(
        task_id=task_id,
        staging_dir=staging,
        records=[_record(task_id, task_object.uri)],
    )

    assert not storage.exists(task_object.uri)
    assert storage.exists(other_object.uri)
    assert not staging.exists()
    assert unrelated.exists()


def test_cleanup_failed_task_rejects_staging_outside_output_root(tmp_path):
    task_id = uuid4()
    storage, lifecycle = _service(tmp_path)
    stored = storage.put(
        __import__("io").BytesIO(b"task"),
        key=f"tasks/{task_id}/charts/{uuid4()}_task.png",
        content_type="image/png",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(StorageError) as exc_info:
        lifecycle.cleanup_failed_task(
            task_id=task_id,
            staging_dir=outside,
            records=[_record(task_id, stored.uri)],
        )

    assert exc_info.value.code is StorageErrorCode.INVALID_URI
    assert sentinel.exists()
    assert not storage.exists(stored.uri)
    assert str(outside) not in str(exc_info.value)


def test_delete_task_is_idempotent_and_does_not_delete_other_task(tmp_path):
    task_id = uuid4()
    other_task_id = uuid4()
    storage, lifecycle = _service(tmp_path)
    stored = storage.put(
        __import__("io").BytesIO(b"task"),
        key=f"tasks/{task_id}/charts/{uuid4()}_task.png",
        content_type="image/png",
    )
    other = storage.put(
        __import__("io").BytesIO(b"other"),
        key=f"tasks/{other_task_id}/charts/{uuid4()}_other.png",
        content_type="image/png",
    )
    records = [_record(task_id, stored.uri)]

    lifecycle.delete_task(task_id=task_id, records=records)
    lifecycle.delete_task(task_id=task_id, records=records)

    assert not storage.exists(stored.uri)
    assert storage.exists(other.uri)


def test_delete_task_treats_string_missing_code_as_idempotent(tmp_path):
    task_id = uuid4()
    storage = LocalFileStorage(tmp_path / "objects")
    lifecycle = StorageLifecycleService(storage=storage, output_root=tmp_path / "outputs")
    missing_uri = f"local://tasks/{task_id}/charts/{uuid4()}_missing.png"

    class StringMissingStorage:
        def delete(self, uri):
            raise StorageError("OBJECT_NOT_FOUND", "missing")

    lifecycle._storage = StringMissingStorage()

    lifecycle.delete_task(task_id=task_id, records=[_record(task_id, missing_uri)])


def test_retention_cleanup_deletes_only_objects_older_than_cutoff(tmp_path):
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    task_id = uuid4()
    storage, lifecycle = _service(tmp_path, retention_days=30, now=lambda: now)
    old = storage.put(
        __import__("io").BytesIO(b"old"),
        key=f"tasks/{task_id}/charts/{uuid4()}_old.png",
        content_type="image/png",
    )
    boundary = storage.put(
        __import__("io").BytesIO(b"boundary"),
        key=f"tasks/{task_id}/charts/{uuid4()}_boundary.png",
        content_type="image/png",
    )
    recent = storage.put(
        __import__("io").BytesIO(b"recent"),
        key=f"tasks/{task_id}/charts/{uuid4()}_recent.png",
        content_type="image/png",
    )
    records = [
        _record(task_id, old.uri, created_at=now - timedelta(days=31)),
        _record(task_id, boundary.uri, created_at=now - timedelta(days=30)),
        _record(task_id, recent.uri, created_at=now - timedelta(days=1)),
    ]

    deleted = lifecycle.cleanup_expired(records)

    assert [record.file_path for record in deleted] == [old.uri]
    assert not storage.exists(old.uri)
    assert storage.exists(boundary.uri)
    assert storage.exists(recent.uri)
