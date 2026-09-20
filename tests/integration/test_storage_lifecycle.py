from io import BytesIO
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.storage import LocalFileStorage
from data_analysis_agent.storage.artifacts import ArtifactStorageService
from data_analysis_agent.storage.lifecycle import StorageLifecycleService


class MemoryArtifactRepository:
    def add(self, record):
        return record


def test_storage_lifecycle_preserves_unrelated_task_and_directory(tmp_path):
    task_id = uuid4()
    unrelated_task_id = uuid4()
    output_root = tmp_path / "outputs"
    storage = LocalFileStorage(tmp_path / "objects")
    artifact_storage = ArtifactStorageService(
        storage=storage,
        artifact_repository=MemoryArtifactRepository(),
    )
    lifecycle = StorageLifecycleService(
        storage=storage,
        artifact_storage=artifact_storage,
        output_root=output_root,
    )
    source = tmp_path / "chart.png"
    source.write_bytes(b"chart")
    current = artifact_storage.store_file(
        task_id=task_id,
        source_path=source,
        artifact_type="CHART",
        filename="chart.png",
        mime_type="image/png",
    )
    unrelated_source = tmp_path / "unrelated.png"
    unrelated_source.write_bytes(b"unrelated")
    unrelated = artifact_storage.store_file(
        task_id=unrelated_task_id,
        source_path=unrelated_source,
        artifact_type="CHART",
        filename="unrelated.png",
        mime_type="image/png",
    )
    staging = output_root / "failed-session"
    staging.mkdir(parents=True)
    (staging / "partial.txt").write_text("partial", encoding="utf-8")
    keep_dir = output_root / "keep-session"
    keep_dir.mkdir(parents=True)
    (keep_dir / "keep.txt").write_text("keep", encoding="utf-8")

    lifecycle.cleanup_failed_task(
        task_id=task_id,
        staging_dir=staging,
        records=[current],
    )

    assert not storage.exists(current.file_path)
    assert storage.exists(unrelated.file_path)
    assert not staging.exists()
    assert (keep_dir / "keep.txt").exists()
