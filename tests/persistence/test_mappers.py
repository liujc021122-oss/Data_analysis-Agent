from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.domain.models import AnalysisTask, TaskEvent
from data_analysis_agent.persistence.mappers import (
    event_to_record,
    record_to_event,
    record_to_task,
    task_to_record,
)
from data_analysis_agent.persistence.models import AnalysisTaskRecord, TaskEventRecord


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


def test_invalid_persistence_status_is_reported_as_mapping_error():
    record = AnalysisTaskRecord(
        task_id=uuid4(),
        query="分析",
        status="BROKEN",
    )

    with pytest.raises(PersistenceMappingError, match="status"):
        record_to_task(record)


def test_persistence_records_forbid_unknown_columns():
    with pytest.raises(ValidationError):
        AnalysisTaskRecord(query="分析", status="PENDING", unknown_column=True)
