from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.domain.models import AnalysisTask, ChartArtifact, ReportArtifact, TaskEvent
from data_analysis_agent.persistence.mappers import (
    artifact_to_record,
    event_to_record,
    record_to_event,
    record_to_task,
    task_to_record,
)
from data_analysis_agent.persistence.models import AnalysisTaskRecord, ArtifactRecord, ReportRecord, TaskEventRecord
from data_analysis_agent.domain.enums import ReportFormat


class ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError("metadata access failed")

    def __iter__(self) -> Iterator[str]:
        return iter(("broken",))

    def __len__(self) -> int:
        return 1


def test_task_record_is_distinct_and_round_trips_domain_values():
    first_dataset = uuid4()
    second_dataset = uuid4()
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    task = AnalysisTask(
        query="分析销售数据",
        dataset_ids=(first_dataset, second_dataset),
        status=TaskStatus.ANALYZING,
        max_rounds=15,
        created_at=now,
        updated_at=now,
        error_code="",
        metadata={"source": "test", "count": 2},
    )

    record = task_to_record(task)
    restored = record_to_task(record)

    assert isinstance(record, AnalysisTaskRecord)
    assert type(record) is not type(task)
    assert record.status == "ANALYZING"
    assert record.dataset_ids_json == [str(first_dataset), str(second_dataset)]
    assert restored == task


def test_task_event_record_round_trips_shared_enum_values():
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    event = TaskEvent(
        task_id=uuid4(),
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=TaskStatus.PENDING,
        to_status=TaskStatus.QUEUED,
        message="queued",
        occurred_at=now,
        metadata={"worker": "offline"},
    )

    record = event_to_record(event)
    restored = record_to_event(record)

    assert isinstance(record, TaskEventRecord)
    assert record.event_type == "STATUS_CHANGED"
    assert record.from_status == "PENDING"
    assert record.to_status == "QUEUED"
    assert restored == event


def test_task_to_record_recursively_normalizes_metadata_for_json():
    nested_id = uuid4()
    nested_time = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    task = AnalysisTask(
        query="分析",
        metadata={
            "nested": {
                "ids": (nested_id,),
                "when": nested_time,
                "status": TaskStatus.PENDING,
                "tags": frozenset({"offline"}),
            }
        },
    )

    record = task_to_record(task)

    assert record.metadata_json == {
        "nested": {
            "ids": [str(nested_id)],
            "when": nested_time.isoformat(),
            "status": "PENDING",
            "tags": ["offline"],
        }
    }
    assert record.model_dump_json()


def test_event_to_record_recursively_normalizes_metadata_for_json():
    nested_id = uuid4()
    nested_time = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    event = TaskEvent(
        task_id=uuid4(),
        event_type=TaskEventType.STATUS_CHANGED,
        to_status=TaskStatus.QUEUED,
        metadata={
            "nested": {
                "ids": (nested_id,),
                "when": nested_time,
                "status": TaskStatus.QUEUED,
            }
        },
    )

    record = event_to_record(event)

    assert record.metadata_json == {
        "nested": {
            "ids": [str(nested_id)],
            "when": nested_time.isoformat(),
            "status": "QUEUED",
        }
    }
    assert record.model_dump_json()


def test_invalid_persistence_status_is_reported_as_mapping_error():
    record = AnalysisTaskRecord(
        task_id=uuid4(),
        query="分析",
        status="BROKEN",
    )

    with pytest.raises(PersistenceMappingError, match="status"):
        record_to_task(record)


def test_empty_persistence_from_status_is_reported_as_mapping_error():
    record = TaskEventRecord(
        task_id=uuid4(),
        event_type="STATUS_CHANGED",
        from_status="",
        to_status="QUEUED",
        occurred_at=datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(PersistenceMappingError, match="from_status"):
        record_to_event(record)


def test_record_to_task_converts_unexpected_metadata_errors_to_mapping_error():
    record = AnalysisTaskRecord.model_construct(
        query="分析",
        status="PENDING",
        metadata_json=ExplodingMapping(),
    )

    with pytest.raises(PersistenceMappingError) as error:
        record_to_task(record)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_record_to_event_converts_unexpected_metadata_errors_to_mapping_error():
    record = TaskEventRecord.model_construct(
        task_id=uuid4(),
        event_type="STATUS_CHANGED",
        to_status="QUEUED",
        occurred_at=datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc),
        metadata_json=ExplodingMapping(),
    )

    with pytest.raises(PersistenceMappingError) as error:
        record_to_event(record)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_persistence_records_forbid_unknown_columns():
    with pytest.raises(ValidationError):
        AnalysisTaskRecord(query="分析", status="PENDING", unknown_column=True)


def test_report_record_and_artifact_record_are_json_serializable():
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    report = ReportRecord(
        report_id=uuid4(), artifact_id=uuid4(), task_id=uuid4(),
        format="MARKDOWN", storage_uri="s3://bucket/report.md",
        size_bytes=12, content_hash="sha256:abc", created_at=now,
    )
    artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=report.task_id, artifact_type="REPORT",
        name="report.md", file_path="s3://bucket/report.md", size_bytes=12,
        content_hash="sha256:abc", created_at=now,
    )

    assert json.loads(report.model_dump_json())["size_bytes"] == 12
    assert json.loads(artifact.model_dump_json())["file_path"].startswith("s3://")


def test_artifacts_round_trip_through_records():
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    chart = ChartArtifact(
        filename="chart.png", file_path="s3://bucket/chart.png", size_bytes=8,
        content_hash="sha256:chart", created_at=now,
    )
    report = ReportArtifact(
        format=ReportFormat.MARKDOWN, file_path="s3://bucket/report.md",
        size_bytes=12, created_at=now,
    )

    assert artifact_to_record(chart, task_id=uuid4()).artifact_type == "CHART"
    assert artifact_to_record(report, task_id=uuid4()).artifact_type == "REPORT"
