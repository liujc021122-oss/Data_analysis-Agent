from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest

from data_analysis_agent.persistence.models import ArtifactRecord
from data_analysis_agent.services.authorization import AccessSubject
from data_analysis_agent.domain.enums import UserRole
from data_analysis_agent.storage import LocalFileStorage
from data_analysis_agent.storage.access import FileAccessDeniedError, FileAccessService


class ArtifactLookup:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    def get_for_user(self, artifact_id, user_id):
        self.calls.append((artifact_id, user_id))
        return self.record

    def get_for_subject(self, artifact_id, subject):
        self.calls.append((artifact_id, subject))
        return self.record


def _record(task_id, uri):
    return ArtifactRecord(
        artifact_id=uuid4(),
        task_id=task_id,
        artifact_type="CHART",
        name="chart.png",
        file_path=uri,
        size_bytes=1,
        content_hash="sha256:chart",
        created_at=datetime.now(timezone.utc),
    )


def test_file_access_returns_opaque_expiring_url_for_owner(tmp_path):
    task_id = uuid4()
    owner_id = uuid4()
    storage = LocalFileStorage(tmp_path / "objects", signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"x"),
        key=f"tasks/{task_id}/charts/{uuid4()}_chart.png",
        content_type="image/png",
    )
    record = _record(task_id, stored.uri).model_copy(
        update={"content_hash": stored.checksum, "size_bytes": stored.size_bytes}
    )
    repository = ArtifactLookup(record)
    service = FileAccessService(storage=storage, artifact_repository=repository)

    url = service.create_download_url(record.artifact_id, owner_id, expires_in=60)

    assert url.startswith("local-download://")
    assert str(tmp_path) not in url
    assert stored.uri not in url
    assert repository.calls == [(record.artifact_id, owner_id)]


@pytest.mark.parametrize("record", [None])
def test_file_access_denies_missing_or_unauthorized_artifact_without_leakage(record):
    artifact_id = uuid4()
    owner_id = uuid4()
    repository = ArtifactLookup(record)
    service = FileAccessService(
        storage=LocalFileStorage("outputs/test-access"),
        artifact_repository=repository,
    )

    with pytest.raises(FileAccessDeniedError) as exc_info:
        service.create_download_url(artifact_id, owner_id)

    assert exc_info.value.code == "FILE_ACCESS_DENIED"
    assert str(artifact_id) not in str(exc_info.value)
    assert "outputs" not in str(exc_info.value)


def test_subject_content_token_is_opaque_and_rechecked_before_reading(tmp_path):
    task_id = uuid4()
    owner_id = uuid4()
    subject = AccessSubject(user_id=owner_id, role=UserRole.USER)
    storage = LocalFileStorage(tmp_path / "objects", signing_secret=b"test-secret")
    stored = storage.put(
        BytesIO(b"x"),
        key=f"tasks/{task_id}/charts/{uuid4()}_chart.png",
        content_type="image/png",
    )
    record = _record(task_id, stored.uri).model_copy(
        update={"content_hash": stored.checksum, "size_bytes": stored.size_bytes}
    )
    repository = ArtifactLookup(record)
    service = FileAccessService(
        storage=storage,
        artifact_repository=repository,
        content_signing_secret=b"content-secret",
    )

    token = service.create_content_token_for_subject(record.artifact_id, subject)

    assert token.startswith("app-download://")
    assert stored.uri not in token
    with service.open_download_for_subject(record.artifact_id, subject, token)[1] as stream:
        assert stream.read() == b"x"

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(FileAccessDeniedError):
        service.open_download_for_subject(record.artifact_id, subject, tampered)
