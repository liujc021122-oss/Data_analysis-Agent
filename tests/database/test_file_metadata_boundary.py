from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.persistence.models import UserRecord


def test_artifact_repository_persists_metadata_without_copying_file(uow_factory, tmp_path, monkeypatch):
    external_path = tmp_path / "not-created.png"
    artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=uuid4(), artifact_type="CHART",
        name="trend.png", file_path=str(external_path), size_bytes=2048,
        content_hash="sha256:chart", mime_type="image/png",
        created_at=datetime.now(timezone.utc),
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("repository accessed file contents")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with uow_factory() as uow:
        user_id = uuid4()
        uow.users.ensure(UserRecord(user_id=user_id, created_at=artifact.created_at))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=artifact.task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        uow.artifacts.add(artifact)
        uow.commit()
    assert not external_path.exists()
    with uow_factory() as uow:
        restored = uow.artifacts.get(artifact.artifact_id)
        assert restored.file_path == str(external_path)
        assert restored.size_bytes == 2048
        assert restored.content_hash == "sha256:chart"


def test_large_looking_uri_remains_metadata_string(uow_factory):
    uri = "s3://bucket/" + ("x" * 10000)
    artifact = ArtifactRecord(artifact_id=uuid4(), task_id=uuid4(), artifact_type="CHART",
                              name="x", file_path=uri, created_at=datetime.now(timezone.utc))
    with uow_factory() as uow:
        user_id = uuid4()
        uow.users.ensure(UserRecord(user_id=user_id, created_at=artifact.created_at))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=artifact.task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        uow.artifacts.add(artifact)
        uow.commit()
    with uow_factory() as uow:
        assert uow.artifacts.get(artifact.artifact_id).file_path == uri
