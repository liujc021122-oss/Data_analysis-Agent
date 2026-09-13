from collections.abc import Mapping
from datetime import date, datetime, time
from enum import Enum
from uuid import UUID

from ..domain.enums import ReportFormat, TaskEventType, TaskStatus
from ..domain.errors import PersistenceMappingError
from ..domain.models import AnalysisTask, ChartArtifact, ReportArtifact, TaskEvent
from .models import AnalysisTaskRecord, ArtifactRecord, ReportRecord, TaskEventRecord


def _normalize_json_value(value):
    if isinstance(value, Mapping):
        return {
            _normalize_json_value(key): _normalize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, Enum):
        return _normalize_json_value(value.value)
    if isinstance(value, (datetime, date, time, UUID)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value


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
        metadata_json=_normalize_json_value(task.metadata),
        model_call_count=task.model_call_count,
        model_duration_ms=task.model_duration_ms,
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
            model_call_count=record.model_call_count,
            model_duration_ms=record.model_duration_ms,
        )
    except PersistenceMappingError:
        raise
    except Exception as exc:
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
        metadata_json=_normalize_json_value(event.metadata),
    )


def record_to_event(record: TaskEventRecord) -> TaskEvent:
    try:
        return TaskEvent(
            event_id=record.event_id,
            task_id=record.task_id,
            event_type=_enum_value(TaskEventType, record.event_type, "event_type"),
            from_status=(
                _enum_value(TaskStatus, record.from_status, "from_status")
                if record.from_status is not None
                else None
            ),
            to_status=_enum_value(TaskStatus, record.to_status, "to_status"),
            message=record.message,
            occurred_at=record.occurred_at,
            metadata=dict(record.metadata_json),
        )
    except PersistenceMappingError:
        raise
    except Exception as exc:
        raise PersistenceMappingError(
            "Unable to map TaskEventRecord to TaskEvent"
        ) from exc


def artifact_to_record(artifact: ChartArtifact | ReportArtifact, task_id: UUID) -> ArtifactRecord:
    is_chart = isinstance(artifact, ChartArtifact)
    return ArtifactRecord(
        artifact_id=artifact.artifact_id,
        task_id=task_id,
        artifact_type="CHART" if is_chart else "REPORT",
        name=artifact.filename if is_chart else artifact.file_path.rsplit("/", 1)[-1],
        file_path=artifact.file_path,
        format=None if is_chart else artifact.format.value,
        mime_type=artifact.mime_type if is_chart else None,
        content_hash=artifact.content_hash,
        size_bytes=artifact.size_bytes,
        description=artifact.description if is_chart else artifact.title,
        title=artifact.title if is_chart else None,
        source_tool_call_id=artifact.source_tool_call_id if is_chart else None,
        metadata_json=_normalize_json_value(artifact.metadata),
        created_at=artifact.created_at,
    )


def record_to_chart(record: ArtifactRecord) -> ChartArtifact:
    if record.artifact_type != "CHART":
        raise PersistenceMappingError("Invalid artifact_type in persistence record")
    return ChartArtifact(artifact_id=record.artifact_id, filename=record.name,
        file_path=record.file_path or record.name, mime_type=record.mime_type or "image/png",
        title=record.title, description=record.description, source_tool_call_id=record.source_tool_call_id,
        size_bytes=record.size_bytes, content_hash=record.content_hash,
        created_at=record.created_at, metadata=dict(record.metadata_json))


def record_to_report_artifact(record: ArtifactRecord) -> ReportArtifact:
    if record.artifact_type != "REPORT":
        raise PersistenceMappingError("Invalid artifact_type in persistence record")
    try:
        fmt = ReportFormat(record.format or "")
    except ValueError as exc:
        raise PersistenceMappingError("Invalid format in persistence record") from exc
    return ReportArtifact(artifact_id=record.artifact_id, format=fmt,
        file_path=record.file_path or record.name, title=record.description,
        content_hash=record.content_hash, size_bytes=record.size_bytes,
        created_at=record.created_at, metadata=dict(record.metadata_json))


def report_to_record(report: ReportRecord) -> ReportRecord:
    return report


chart_artifact_to_record = artifact_to_record
report_artifact_to_record = artifact_to_record
