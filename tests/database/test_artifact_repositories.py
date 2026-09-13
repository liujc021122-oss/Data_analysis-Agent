from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.domain.models import AnalysisTask, ExecutionResult, ToolCall
from data_analysis_agent.persistence.errors import EntityNotFoundError
from data_analysis_agent.persistence.models import ArtifactRecord, ReportRecord, UserRecord
from data_analysis_agent.persistence.orm_models import ArtifactORM, ExecutionORM, ReportORM, ToolCallORM


ARTIFACT_PERSISTENCE_NAMESPACE = "__data_analysis_agent_persistence__"
ARTIFACT_TITLE_KEY = "artifact_title"


def test_ancillary_repositories_round_trip(uow_factory):
    user_id, task_id, tool_call_id, artifact_id, report_id = [uuid4() for _ in range(5)]
    now = datetime.now(timezone.utc)
    chart = ArtifactRecord(
        artifact_id=artifact_id, task_id=task_id, artifact_type="CHART", name="trend.png",
        file_path="s3://bucket/trend.png", format=None, mime_type="image/png",
        content_hash="sha256:chart", size_bytes=12, description="Monthly revenue chart",
        title="Revenue trend", source_tool_call_id=tool_call_id,
        metadata_json={"chart": {"axes": ["month", "revenue"]}}, created_at=now,
    )
    report_artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=task_id, artifact_type="REPORT", name="report.md",
        file_path="s3://bucket/report.md", format="MARKDOWN", mime_type="text/markdown",
        content_hash="sha256:report", size_bytes=18, description="Analysis report",
        title="Revenue report", source_tool_call_id=tool_call_id,
        metadata_json={"report": {"sections": ["summary"]}}, created_at=now,
    )
    report = ReportRecord(
        report_id=report_id, artifact_id=report_artifact.artifact_id, task_id=task_id,
        format="MARKDOWN", storage_uri="s3://bucket/report.md", size_bytes=18,
        content_hash="sha256:report", created_at=now,
    )
    call = ToolCall(tool_call_id=tool_call_id, task_id=task_id, tool_name="plot",
                    arguments={"config": {"x": [1, 2]}}, result={"ok": False, "error": "timeout"},
                    status=ToolCallStatus.FAILED, started_at=now, finished_at=now,
                    error_message="timeout")
    execution = ExecutionResult(success=False, output="partial", error="timeout",
                                variables={"rows": 2, "nested": {"values": [1, 2]}}, duration_ms=7)
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="analyze"),
                      idempotency_key="k", request_hash="h")
        returned_call = uow.tool_calls.add(call)
        assert returned_call is not call
        assert returned_call.tool_call_id == tool_call_id
        assert returned_call.task_id == task_id
        assert returned_call.tool_name == "plot"
        assert returned_call.arguments == call.arguments
        assert returned_call.result == call.result
        assert returned_call.status is ToolCallStatus.FAILED
        assert returned_call.started_at == now
        assert returned_call.finished_at == now
        assert returned_call.error_message == "timeout"
        assert returned_call.arguments is not call.arguments
        assert returned_call.arguments["config"] is not call.arguments["config"]
        assert returned_call.result is not call.result
        assert call.arguments["config"]["x"] == (1, 2)
        assert call.result["ok"] is False
        assert call.result["error"] == "timeout"

        returned_execution = uow.executions.add(execution, tool_call_id=tool_call_id)
        assert returned_execution is not execution
        assert returned_execution.success is False
        assert returned_execution.output == "partial"
        assert returned_execution.error == "timeout"
        assert returned_execution.variables == execution.variables
        assert returned_execution.duration_ms == 7
        assert returned_execution.variables is not execution.variables
        assert returned_execution.variables["nested"] is not execution.variables["nested"]
        assert execution.variables["rows"] == 2
        assert execution.variables["nested"]["values"] == (1, 2)
        execution_row = uow.session.scalars(
            select(ExecutionORM).where(ExecutionORM.tool_call_id == tool_call_id)
        ).one()
        assert execution_row.created_at is not None
        assert execution_row.created_at.tzinfo is not None
        assert execution_row.created_at.utcoffset() == timezone.utc.utcoffset(execution_row.created_at)

        returned_chart = uow.artifacts.add(chart)
        assert returned_chart is not chart
        assert returned_chart.artifact_id == artifact_id
        assert returned_chart.task_id == task_id
        assert returned_chart.artifact_type == "CHART"
        assert returned_chart.name == "trend.png"
        assert returned_chart.file_path == "s3://bucket/trend.png"
        assert returned_chart.format is None
        assert returned_chart.mime_type == "image/png"
        assert returned_chart.content_hash == "sha256:chart"
        assert returned_chart.size_bytes == 12
        assert returned_chart.description == "Monthly revenue chart"
        assert returned_chart.title == "Revenue trend"
        assert returned_chart.source_tool_call_id == tool_call_id
        assert returned_chart.metadata_json == chart.metadata_json
        returned_chart.metadata_json["chart"]["axes"].append("legend")
        assert chart.metadata_json["chart"]["axes"] == ["month", "revenue"]
        chart_row = uow.session.get(ArtifactORM, artifact_id)
        assert chart_row.artifact_type == "CHART"
        assert chart_row.name == "trend.png"
        assert chart_row.file_path == "s3://bucket/trend.png"
        assert chart_row.format is None
        assert chart_row.mime_type == "image/png"
        assert chart_row.content_hash == "sha256:chart"
        assert chart_row.size_bytes == 12
        assert chart_row.description == "Monthly revenue chart"
        assert chart_row.source_tool_call_id == tool_call_id
        assert chart_row.metadata_json["chart"]["axes"] == ["month", "revenue"]
        assert chart_row.metadata_json[ARTIFACT_PERSISTENCE_NAMESPACE][ARTIFACT_TITLE_KEY] == "Revenue trend"

        returned_report_artifact = uow.artifacts.add(report_artifact)
        assert returned_report_artifact is not report_artifact
        assert returned_report_artifact.artifact_id == report_artifact.artifact_id
        assert returned_report_artifact.task_id == task_id
        assert returned_report_artifact.artifact_type == "REPORT"
        assert returned_report_artifact.name == "report.md"
        assert returned_report_artifact.file_path == "s3://bucket/report.md"
        assert returned_report_artifact.format == "MARKDOWN"
        assert returned_report_artifact.mime_type == "text/markdown"
        assert returned_report_artifact.content_hash == "sha256:report"
        assert returned_report_artifact.size_bytes == 18
        assert returned_report_artifact.description == "Analysis report"
        assert returned_report_artifact.title == "Revenue report"
        assert returned_report_artifact.source_tool_call_id == tool_call_id
        assert returned_report_artifact.metadata_json == report_artifact.metadata_json
        returned_report = uow.reports.add(report)
        assert returned_report is not report
        assert returned_report.report_id == report_id
        assert returned_report.artifact_id == report_artifact.artifact_id
        assert returned_report.task_id == task_id
        assert returned_report.format == "MARKDOWN"
        assert returned_report.storage_uri == "s3://bucket/report.md"
        assert returned_report.size_bytes == 18
        assert returned_report.content_hash == "sha256:report"
        report_row = uow.session.get(ReportORM, report_id)
        assert report_row.artifact_id == report_artifact.artifact_id
        assert report_row.format.value == "MARKDOWN"
        assert report_row.storage_uri == "s3://bucket/report.md"
        assert report_row.size_bytes == 18
        assert report_row.content_hash == "sha256:report"
        uow.commit()
    with uow_factory() as uow:
        restored_call = uow.tool_calls.get(tool_call_id)
        assert restored_call == call
        assert uow.tool_calls.list_for_task(task_id) == [call]
        assert uow.executions.list_for_tool_call(tool_call_id)[0] == execution
        restored_chart = uow.artifacts.get(artifact_id)
        assert restored_chart == chart
        assert restored_chart.title == "Revenue trend"
        assert restored_chart.description == "Monthly revenue chart"
        assert restored_chart.metadata_json == chart.metadata_json
        assert ARTIFACT_PERSISTENCE_NAMESPACE not in restored_chart.metadata_json
        restored_report_artifact = uow.artifacts.get(report_artifact.artifact_id)
        assert restored_report_artifact == report_artifact
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


def test_report_rejects_artifact_belonging_to_another_task_without_leaking_existence(
    uow_factory,
):
    now = datetime.now(timezone.utc)
    owner_id = uuid4()
    first_task_id, second_task_id, artifact_id = [uuid4() for _ in range(3)]
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=owner_id, created_at=now))
        uow.tasks.add(
            user_id=owner_id,
            task=AnalysisTask(task_id=first_task_id, query="first"),
            idempotency_key="first",
            request_hash="first-hash",
        )
        uow.tasks.add(
            user_id=owner_id,
            task=AnalysisTask(task_id=second_task_id, query="second"),
            idempotency_key="second",
            request_hash="second-hash",
        )
        uow.artifacts.add(
            ArtifactRecord(
                artifact_id=artifact_id,
                task_id=first_task_id,
                artifact_type="REPORT",
                name="first.md",
                file_path="s3://first",
                format="MARKDOWN",
                created_at=now,
            )
        )

        with pytest.raises(EntityNotFoundError, match=rf"artifact {artifact_id} not found"):
            uow.reports.add(
                ReportRecord(
                    report_id=uuid4(),
                    artifact_id=artifact_id,
                    task_id=second_task_id,
                    format="MARKDOWN",
                    storage_uri="s3://second",
                    created_at=now,
                )
            )
        assert uow.session.scalars(select(ReportORM)).all() == []
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


def test_artifact_metadata_namespace_collision_is_rejected(uow_factory):
    now = datetime.now(timezone.utc)
    user_id, task_id = uuid4(), uuid4()
    artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=task_id, artifact_type="CHART", name="chart.png",
        file_path="s3://chart", metadata_json={ARTIFACT_PERSISTENCE_NAMESPACE: {"user": "value"}},
        created_at=now,
    )
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        with pytest.raises(PersistenceMappingError, match="reserved"):
            uow.artifacts.add(artifact)
        uow.rollback()


def test_malformed_artifact_metadata_namespace_is_rejected(uow_factory):
    now = datetime.now(timezone.utc)
    user_id, task_id = uuid4(), uuid4()
    artifact = ArtifactRecord(artifact_id=uuid4(), task_id=task_id, artifact_type="CHART",
                              name="chart.png", file_path="s3://chart", created_at=now)
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(user_id=user_id, task=AnalysisTask(task_id=task_id, query="q"),
                      idempotency_key="k", request_hash="h")
        uow.artifacts.add(artifact)
        row = uow.session.get(ArtifactORM, artifact.artifact_id)
        row.metadata_json = {ARTIFACT_PERSISTENCE_NAMESPACE: None}
        with pytest.raises(PersistenceMappingError, match="namespace"):
            uow.artifacts.get(artifact.artifact_id)
        uow.rollback()


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
