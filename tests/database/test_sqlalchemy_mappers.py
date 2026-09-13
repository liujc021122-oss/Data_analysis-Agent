import json
from datetime import datetime, timezone
from uuid import uuid4

from data_analysis_agent.domain.enums import ReportFormat, TaskEventType, TaskStatus, ToolCallStatus
from data_analysis_agent.persistence.orm_mappers import (
    artifact_orm_to_record, dataset_orm_to_record, event_orm_to_record,
    execution_orm_to_record, report_orm_to_record, task_orm_to_record,
    tool_call_orm_to_record, user_orm_to_record,
    artifact_record_to_orm, dataset_record_to_orm, event_record_to_orm,
    execution_record_to_orm, report_record_to_orm, task_record_to_orm,
    tool_call_record_to_orm, user_record_to_orm,
)
from data_analysis_agent.persistence.orm_models import (
    AnalysisTaskORM, ArtifactORM, DatasetORM, ExecutionORM, ReportORM,
    TaskEventORM, ToolCallORM, UserORM,
)


def test_each_orm_row_maps_to_json_serializable_record():
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    user_id, dataset_id, task_id, tool_id = (uuid4() for _ in range(4))
    user = UserORM(user_id=user_id, created_at=now)
    dataset = DatasetORM(dataset_id=dataset_id, user_id=user_id, name="x.csv", source_uri="s3://x", content_type="text/csv", created_at=now, metadata_json={})
    task = AnalysisTaskORM(task_id=task_id, user_id=user_id, idempotency_key="k", request_hash="h", query="分析", status=TaskStatus.PENDING, max_rounds=10, created_at=now, updated_at=now, metadata_json={}, model_call_count=2, model_duration_ms=35)
    event = TaskEventORM(event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED, from_status=TaskStatus.PENDING, to_status=TaskStatus.QUEUED, occurred_at=now, metadata_json={})
    tool = ToolCallORM(tool_call_id=tool_id, task_id=task_id, tool_name="plot", arguments_json={}, result_json=None, status=ToolCallStatus.PENDING)
    execution = ExecutionORM(execution_result_id=uuid4(), tool_call_id=tool_id, success=True, output_text="ok", error_text=None, variables_json={}, duration_ms=4, created_at=now)
    artifact = ArtifactORM(artifact_id=uuid4(), task_id=task_id, artifact_type="REPORT", name="report.md", file_path="s3://report", format=ReportFormat.MARKDOWN, mime_type=None, content_hash="h", size_bytes=12, description=None, source_tool_call_id=None, metadata_json={}, created_at=now)
    report = ReportORM(report_id=uuid4(), artifact_id=artifact.artifact_id, task_id=task_id, format=ReportFormat.MARKDOWN, storage_uri="s3://report", size_bytes=12, content_hash="h", created_at=now)

    records = [user_orm_to_record(user), dataset_orm_to_record(dataset), task_orm_to_record(task, [dataset_id]), event_orm_to_record(event), tool_call_orm_to_record(tool), execution_orm_to_record(execution), artifact_orm_to_record(artifact), report_orm_to_record(report)]
    assert all(json.loads(record.model_dump_json()) for record in records)
    assert task_orm_to_record(task, [dataset_id]).status == "PENDING"
    assert event_orm_to_record(event).event_type == "STATUS_CHANGED"
    assert artifact_orm_to_record(artifact).format == "MARKDOWN"
    round_tripped = [
        user_record_to_orm(records[0]), dataset_record_to_orm(records[1]),
        task_record_to_orm(records[2]), event_record_to_orm(records[3]),
        tool_call_record_to_orm(records[4]), execution_record_to_orm(records[5]),
        artifact_record_to_orm(records[6]), report_record_to_orm(records[7]),
    ]
    assert all(type(row).__name__.endswith("ORM") for row in round_tripped)
