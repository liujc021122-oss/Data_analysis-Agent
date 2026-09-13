from pathlib import Path
from uuid import uuid4

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import utc_now
from data_analysis_agent.persistence import (
    ArtifactRecord,
    Database,
    DatasetRecord,
    ReportRecord,
    UnitOfWork,
    UserRecord,
    init_database,
)
from data_analysis_agent.services import TaskPersistenceService

from tests.database.conftest import settings_for


def test_task_and_metadata_survive_engine_and_session_restart(tmp_path: Path):
    database_path = tmp_path / "persistent.sqlite3"
    database_url = f"sqlite:///{database_path}"
    user_id = uuid4()
    dataset_id = uuid4()
    csv_path = tmp_path / "supplied.csv"
    request = AnalysisTaskCreateRequest(
        query="分析销售趋势",
        idempotency_key="restart-key",
        dataset_ids=(dataset_id,),
    )

    first = Database.from_settings(settings_for(database_url))
    init_database(first.engine)
    with UnitOfWork(first.session_factory) as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=utc_now()))
        uow.datasets.add(DatasetRecord(
            dataset_id=dataset_id,
            user_id=user_id,
            name="sales.csv",
            source_uri=str(csv_path),
            created_at=utc_now(),
        ))
        uow.commit()
    service = TaskPersistenceService(lambda: UnitOfWork(first.session_factory))

    task = service.create_task(user_id=user_id, request=request)
    with UnitOfWork(first.session_factory) as uow:
        artifact = uow.artifacts.add(ArtifactRecord(
            task_id=task.task_id,
            artifact_type="CHART",
            name="chart.png",
            file_path="s3://bucket/chart.png",
            mime_type="image/png",
            size_bytes=2048,
            content_hash="sha256:chart",
            created_at=task.created_at,
        ))
        uow.reports.add(ReportRecord(
            artifact_id=artifact.artifact_id,
            task_id=task.task_id,
            format="MARKDOWN",
            storage_uri="s3://bucket/report.md",
            size_bytes=4096,
            content_hash="sha256:report",
            created_at=task.created_at,
        ))
        uow.commit()
    first.engine.dispose()

    second = Database.from_settings(settings_for(database_url))
    try:
        with UnitOfWork(second.session_factory) as uow:
            restored = uow.tasks.get(task.task_id)
            events = uow.task_events.list_for_task(task.task_id)
            artifacts = uow.artifacts.list_for_task(task.task_id)
            reports = uow.reports.list_for_task(task.task_id)
            datasets = uow.datasets.list_for_user(user_id)

        assert restored.task_id == task.task_id
        assert restored.status is TaskStatus.PENDING
        assert events[0].to_status is TaskStatus.PENDING
        assert artifacts[0].file_path == "s3://bucket/chart.png"
        assert reports[0].storage_uri == "s3://bucket/report.md"
        assert datasets[0].source_uri == str(csv_path)
        assert database_path.exists()
        assert not csv_path.exists()
        assert [path.name for path in tmp_path.iterdir()] == ["persistent.sqlite3"]
    finally:
        second.engine.dispose()
