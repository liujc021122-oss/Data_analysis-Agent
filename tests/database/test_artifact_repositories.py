from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from data_analysis_agent.domain.enums import ReportFormat, ToolCallStatus
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.domain.models import AnalysisTask, ExecutionResult, ToolCall
from data_analysis_agent.persistence.errors import EntityNotFoundError
from data_analysis_agent.persistence.models import ArtifactRecord, ReportRecord, UserRecord


def test_ancillary_repositories_round_trip(uow_factory):
    user_id, task_id, tool_call_id, artifact_id, report_id = [uuid4() for _ in range(5)]
    now = datetime.now(timezone.utc)
    chart = ArtifactRecord(
        artifact_id=artifact_id, task_id=task_id, artifact_type="CHART", name="trend.png",
        file_path="s3://bucket/trend.png", format=None, mime_type="image/png",
        content_hash="sha256:chart", size_bytes=12, created_at=now,
    )
    report_artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=task_id, artifact_type="REPORT", name="report.md",
        file_path="s3://bucket/report.md", format="MARKDOWN", mime_type=None,
        content_hash="sha256:report", size_bytes=18, created_at=now,
    )
    report = ReportRecord(
        report_id=report_id, artifact_id=report_artifact.artifact_id, task_id=task_id,
        format="MARKDOWN", storage_uri="s3://bucket/report.md", size_bytes=18,
        content_hash="sha256:report", created_at=now,
    )
    call = ToolCall(tool_call_id=tool_call_id, task_id=task_id, tool_name="plot",
                    arguments={"x": [1, 2]}, result={"ok": True},
                    status=ToolCallStatus.SUCCEEDED, started_at=now, finished_at=now,
                    error_message=None)
    execution = ExecutionResult(success=True, output="done", variables={"rows": 2}, duration_ms=7)
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="analyze"),
                      idempotency_key="k", request_hash="h")
        assert uow.tool_calls.add(call).tool_call_id == tool_call_id
        assert uow.executions.add(execution, tool_call_id=tool_call_id).success
        uow.artifacts.add(chart)
        uow.artifacts.add(report_artifact)
        uow.reports.add(report)
        uow.commit()
    with uow_factory() as uow:
        assert uow.tool_calls.get(tool_call_id) == call
        assert uow.tool_calls.list_for_task(task_id) == [call]
        assert uow.executions.list_for_tool_call(tool_call_id)[0] == execution
        assert uow.artifacts.get(artifact_id) == chart
        assert uow.artifacts.list_for_task(task_id) == sorted(
            [chart, report_artifact], key=lambda item: (item.created_at, item.artifact_id)
        )
        assert uow.reports.get(report_id) == report
        assert uow.reports.list_for_task(task_id) == [report]


def test_ancillary_foreign_keys_and_report_uniqueness(uow_factory):
    now = datetime.now(timezone.utc)
    artifact_id, task_id = uuid4(), uuid4()
    report = ReportRecord(report_id=uuid4(), artifact_id=artifact_id, task_id=task_id,
                          format="MARKDOWN", storage_uri="s3://report", created_at=now)
    with uow_factory() as uow:
        with pytest.raises(EntityNotFoundError):
            uow.reports.add(report)
        uow.rollback()

    with uow_factory() as uow:
        user_id = uuid4()
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        artifact = ArtifactRecord(artifact_id=artifact_id, task_id=task_id, artifact_type="REPORT",
                                   name="r.md", file_path="s3://r", format="MARKDOWN", created_at=now)
        uow.artifacts.add(artifact)
        uow.reports.add(report)
        with pytest.raises(IntegrityError):
            uow.reports.add(report.model_copy(update={"report_id": uuid4()}))
        uow.rollback()


def test_invalid_enum_rows_raise_mapping_error(uow_factory):
    with uow_factory() as uow:
        user_id, task_id = uuid4(), uuid4()
        uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        row = __import__("data_analysis_agent.persistence.orm_models", fromlist=["ToolCallORM"]).ToolCallORM(
            tool_call_id=uuid4(), task_id=task_id, tool_name="x", arguments_json={}, status="PENDING"
        )
        uow.session.add(row)
        uow.session.flush()
        row.status = "BROKEN"
        with pytest.raises(PersistenceMappingError):
            uow.tool_calls.get(row.tool_call_id)
        uow.rollback()


def test_execution_rejects_negative_duration_before_flush(uow_factory):
    with uow_factory() as uow:
        with pytest.raises(ValueError):
            uow.executions.add(ExecutionResult(success=True, duration_ms=-1))
        assert not uow.session.new


def test_invalid_report_format_row_raises_mapping_error(uow_factory):
    now = datetime.now(timezone.utc)
    user_id, task_id, artifact_id, report_id = [uuid4() for _ in range(4)]
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        uow.artifacts.add(ArtifactRecord(artifact_id=artifact_id, task_id=task_id,
                                         artifact_type="REPORT", name="r.md", file_path="s3://r",
                                         format="MARKDOWN", created_at=now))
        report = uow.reports.add(ReportRecord(report_id=report_id, artifact_id=artifact_id,
                                              task_id=task_id, format="MARKDOWN",
                                              storage_uri="s3://r", created_at=now))
        row = uow.session.get(__import__("data_analysis_agent.persistence.orm_models", fromlist=["ReportORM"]).ReportORM, report.report_id)
        row.format = "BROKEN"
        with pytest.raises(PersistenceMappingError):
            uow.reports.get(report_id)
        uow.rollback()
