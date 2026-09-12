from uuid import UUID

from pydantic import ValidationError

from ..domain.enums import TaskEventType, TaskStatus
from ..domain.errors import PersistenceMappingError
from ..domain.models import AnalysisTask, TaskEvent
from .models import AnalysisTaskRecord, TaskEventRecord


def _enum_value(enum_type, value, field_name):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise PersistenceMappingError(
            f"Invalid {field_name} in persistence record"
        ) from exc


def task_to_record(task: AnalysisTask) -> AnalysisTaskRecord:
    return AnalysisTaskRecord(
        task_id=task.task_id,
        query=task.query,
        dataset_ids_json=[str(dataset_id) for dataset_id in task.dataset_ids],
        status=task.status.value,
        max_rounds=task.max_rounds,
        created_at=task.created_at,
        updated_at=task.updated_at,
        error_code=task.error_code,
        error_message=task.error_message,
        metadata_json=dict(task.metadata),
    )


def record_to_task(record: AnalysisTaskRecord) -> AnalysisTask:
    try:
        status = _enum_value(TaskStatus, record.status, "status")
        dataset_ids = tuple(UUID(record_id) for record_id in record.dataset_ids_json)
        return AnalysisTask(
            task_id=record.task_id,
            query=record.query,
            dataset_ids=dataset_ids,
            status=status,
            max_rounds=record.max_rounds,
            created_at=record.created_at,
            updated_at=record.updated_at,
            error_code=record.error_code,
            error_message=record.error_message,
            metadata=dict(record.metadata_json),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, PersistenceMappingError):
            raise
        raise PersistenceMappingError(
            "Unable to map AnalysisTaskRecord to AnalysisTask"
        ) from exc


def event_to_record(event: TaskEvent) -> TaskEventRecord:
    return TaskEventRecord(
        event_id=event.event_id,
        task_id=event.task_id,
        event_type=event.event_type.value,
        from_status=event.from_status.value if event.from_status else None,
        to_status=event.to_status.value,
        message=event.message,
        occurred_at=event.occurred_at,
        metadata_json=dict(event.metadata),
    )


def record_to_event(record: TaskEventRecord) -> TaskEvent:
    try:
        return TaskEvent(
            event_id=record.event_id,
            task_id=record.task_id,
            event_type=_enum_value(TaskEventType, record.event_type, "event_type"),
            from_status=(
                _enum_value(TaskStatus, record.from_status, "from_status")
                if record.from_status
                else None
            ),
            to_status=_enum_value(TaskStatus, record.to_status, "to_status"),
            message=record.message,
            occurred_at=record.occurred_at,
            metadata=dict(record.metadata_json),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise PersistenceMappingError(
            "Unable to map TaskEventRecord to TaskEvent"
        ) from exc
