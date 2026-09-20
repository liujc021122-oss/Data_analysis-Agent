from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.storage import LocalFileStorage
from data_analysis_agent.storage.artifacts import ArtifactStorageService
from data_analysis_agent.storage.errors import StorageErrorCode
from data_analysis_agent.storage.lifecycle import StorageLifecycleService


class MemoryArtifactRepository:
    def add(self, record):
        return record


def _record(task_id, uri, *, size_bytes, content_hash):
    return ArtifactRecord(
        artifact_id=uuid4(),
        task_id=task_id,
        artifact_type="CHART",
        name="chart.png",
        file_path=uri,
        size_bytes=size_bytes,
        content_hash=content_hash,
        created_at=datetime.now(timezone.utc),
    )


def test_reconcile_reports_missing_and_mismatched_objects_without_paths(tmp_path):
    task_id = uuid4()
    storage = LocalFileStorage(tmp_path / "objects")
    artifact_storage = ArtifactStorageService(
        storage=storage,
        artifact_repository=MemoryArtifactRepository(),
    )
    lifecycle = StorageLifecycleService(
        storage=storage,
        artifact_storage=artifact_storage,
        output_root=tmp_path / "outputs",
    )
    stored = storage.put(
        BytesIO(b"actual"),
        key=f"tasks/{task_id}/charts/{uuid4()}_chart.png",
        content_type="image/png",
    )
    mismatch = _record(
        task_id,
        stored.uri,
        size_bytes=999,
        content_hash="sha256:wrong",
    )
    missing_uri = f"local://tasks/{task_id}/charts/{uuid4()}_missing.png"
    missing = _record(task_id, missing_uri, size_bytes=1, content_hash="sha256:missing")

    issues = lifecycle.reconcile([mismatch, missing])

    assert [issue.code for issue in issues] == [
        StorageErrorCode.METADATA_MISMATCH.value,
        StorageErrorCode.OBJECT_NOT_FOUND.value,
    ]
    assert [issue.artifact_id for issue in issues] == [
        mismatch.artifact_id,
        missing.artifact_id,
    ]
    assert all(str(tmp_path) not in issue.message for issue in issues)
    assert all(stored.uri not in issue.message for issue in issues)


def test_reconcile_returns_no_issues_for_matching_object(tmp_path):
    task_id = uuid4()
    storage = LocalFileStorage(tmp_path / "objects")
    artifact_storage = ArtifactStorageService(
        storage=storage,
        artifact_repository=MemoryArtifactRepository(),
    )
    stored = storage.put(
        BytesIO(b"actual"),
        key=f"tasks/{task_id}/charts/{uuid4()}_chart.png",
        content_type="image/png",
    )
    lifecycle = StorageLifecycleService(
        storage=storage,
        artifact_storage=artifact_storage,
        output_root=tmp_path / "outputs",
    )

    assert lifecycle.reconcile(
        [
            _record(
                task_id,
                stored.uri,
                size_bytes=stored.size_bytes,
                content_hash=stored.checksum,
            )
        ]
    ) == []
