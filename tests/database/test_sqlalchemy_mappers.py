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


def test_all_orm_mappers_round_trip_fields_and_isolate_nested_json():
    now = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
    user_id, dataset_id, task_id, tool_id = (uuid4() for _ in range(4))
    nested = {"columns": ["id", {"name": "amount"}]}
    rows = [
        UserORM(user_id=user_id, created_at=now),
        DatasetORM(dataset_id=dataset_id, user_id=user_id, name="sales.csv", source_uri="s3://bucket/sales.csv", content_type="text/csv", size_bytes=42, checksum="sha256:data", created_at=now, metadata_json=nested),
        AnalysisTaskORM(task_id=task_id, user_id=user_id, idempotency_key="idem-1", request_hash="sha256:req", query="分析销售", status=TaskStatus.ANALYZING, max_rounds=7, created_at=now, updated_at=now, error_code="E1", error_message="warning", metadata_json={"nested": nested}, model_call_count=3, model_duration_ms=99),
        TaskEventORM(event_id=uuid4(), task_id=task_id, event_type=TaskEventType.STATUS_CHANGED, from_status=TaskStatus.RUNNING, to_status=TaskStatus.ANALYZING, message="started", occurred_at=now, metadata_json={"nested": nested}),
        ToolCallORM(tool_call_id=tool_id, task_id=task_id, tool_name="plot", arguments_json={"config": nested}, result_json={"rows": [1]}, status=ToolCallStatus.SUCCEEDED, started_at=now, finished_at=now, error_message=None),
        ExecutionORM(execution_result_id=uuid4(), tool_call_id=tool_id, success=False, output_text="", error_text="failed", variables_json={"nested": nested}, duration_ms=11, created_at=now),
        ArtifactORM(artifact_id=uuid4(), task_id=task_id, artifact_type="CHART", name="sales.png", file_path="s3://bucket/sales.png", format=None, mime_type="image/png", content_hash="sha256:img", size_bytes=128, description="chart desc", source_tool_call_id=tool_id, metadata_json={"nested": nested}, created_at=now),
        ReportORM(report_id=uuid4(), artifact_id=uuid4(), task_id=task_id, format=ReportFormat.DOCX, storage_uri="s3://bucket/report.docx", size_bytes=256, content_hash="sha256:report", created_at=now),
    ]
    records = [
        user_orm_to_record(rows[0]), dataset_orm_to_record(rows[1]), task_orm_to_record(rows[2], [dataset_id]),
        event_orm_to_record(rows[3]), tool_call_orm_to_record(rows[4]), execution_orm_to_record(rows[5]),
        artifact_orm_to_record(rows[6]), report_orm_to_record(rows[7]),
    ]
    inverse = [
        user_record_to_orm(records[0]), dataset_record_to_orm(records[1]), task_record_to_orm(records[2]),
        event_record_to_orm(records[3]), tool_call_record_to_orm(records[4]), execution_record_to_orm(records[5]),
        artifact_record_to_orm(records[6]), report_record_to_orm(records[7]),
    ]

    assert inverse[0] is not rows[0] and (inverse[0].user_id, inverse[0].created_at) == (user_id, now)
    assert inverse[1] is not rows[1] and (inverse[1].dataset_id, inverse[1].user_id, inverse[1].name, inverse[1].source_uri, inverse[1].content_type, inverse[1].size_bytes, inverse[1].checksum, inverse[1].created_at) == (dataset_id, user_id, rows[1].name, rows[1].source_uri, rows[1].content_type, rows[1].size_bytes, rows[1].checksum, now)
    assert inverse[2] is not rows[2] and (inverse[2].task_id, inverse[2].user_id, inverse[2].query, inverse[2].status, inverse[2].max_rounds, inverse[2].created_at, inverse[2].updated_at, inverse[2].error_code, inverse[2].error_message, inverse[2].idempotency_key, inverse[2].request_hash, inverse[2].model_call_count, inverse[2].model_duration_ms) == (task_id, user_id, rows[2].query, "ANALYZING", 7, now, now, "E1", "warning", "idem-1", "sha256:req", 3, 99)
    assert inverse[3] is not rows[3] and (inverse[3].event_id, inverse[3].task_id, inverse[3].event_type, inverse[3].from_status, inverse[3].to_status, inverse[3].message, inverse[3].occurred_at) == (rows[3].event_id, task_id, "STATUS_CHANGED", "RUNNING", "ANALYZING", "started", now)
    assert inverse[4] is not rows[4] and (inverse[4].tool_call_id, inverse[4].task_id, inverse[4].tool_name, inverse[4].status, inverse[4].started_at, inverse[4].finished_at, inverse[4].error_message) == (tool_id, task_id, "plot", "SUCCEEDED", now, now, None)
    assert inverse[5] is not rows[5] and (inverse[5].execution_result_id, inverse[5].tool_call_id, inverse[5].success, inverse[5].output_text, inverse[5].error_text, inverse[5].duration_ms) == (rows[5].execution_result_id, tool_id, False, "", "failed", 11)
    assert inverse[6] is not rows[6] and (inverse[6].artifact_id, inverse[6].task_id, inverse[6].artifact_type, inverse[6].name, inverse[6].file_path, inverse[6].format, inverse[6].mime_type, inverse[6].content_hash, inverse[6].size_bytes, inverse[6].description, inverse[6].source_tool_call_id, inverse[6].created_at) == (rows[6].artifact_id, task_id, "CHART", "sales.png", "s3://bucket/sales.png", None, "image/png", "sha256:img", 128, "chart desc", tool_id, now)
    assert inverse[7] is not rows[7] and (inverse[7].report_id, inverse[7].artifact_id, inverse[7].task_id, inverse[7].format, inverse[7].storage_uri, inverse[7].size_bytes, inverse[7].content_hash, inverse[7].created_at) == (rows[7].report_id, rows[7].artifact_id, task_id, "DOCX", "s3://bucket/report.docx", 256, "sha256:report", now)
    assert records[2].dataset_ids_json == [str(dataset_id)]
    for index in (1, 2, 3, 4, 5, 6):
        assert records[index].model_dump() != {}
    records[1].metadata_json["columns"].append("record mutation")
    records[2].metadata_json["nested"]["columns"].append("record mutation")
    records[3].metadata_json["nested"]["columns"].append("record mutation")
    records[4].arguments_json["config"]["columns"].append("record mutation")
    records[5].variables_json["nested"]["columns"].append("record mutation")
    records[6].metadata_json["nested"]["columns"].append("record mutation")
    assert rows[1].metadata_json == nested
    assert rows[2].metadata_json == {"nested": nested}
    assert rows[3].metadata_json == {"nested": nested}
    assert rows[4].arguments_json == {"config": nested}
    assert rows[5].variables_json == {"nested": nested}
    assert rows[6].metadata_json == {"nested": nested}
    inverse[1].metadata_json["columns"].append("orm mutation")
    inverse[2].metadata_json["nested"]["columns"].append("orm mutation")
    inverse[3].metadata_json["nested"]["columns"].append("orm mutation")
    inverse[4].arguments_json["config"]["columns"].append("orm mutation")
    inverse[5].variables_json["nested"]["columns"].append("orm mutation")
    inverse[6].metadata_json["nested"]["columns"].append("orm mutation")
    assert records[1].metadata_json["columns"] == ["id", {"name": "amount"}, "record mutation"]
    assert records[2].metadata_json["nested"]["columns"] == ["id", {"name": "amount"}, "record mutation"]
    assert records[3].metadata_json["nested"]["columns"] == ["id", {"name": "amount"}, "record mutation"]
    assert records[4].arguments_json["config"]["columns"] == ["id", {"name": "amount"}, "record mutation"]
    assert records[5].variables_json["nested"]["columns"] == ["id", {"name": "amount"}, "record mutation"]
    assert records[6].metadata_json["nested"]["columns"] == ["id", {"name": "amount"}, "record mutation"]
