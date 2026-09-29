from copy import deepcopy
from collections.abc import Mapping
from uuid import UUID

from ..domain.errors import PersistenceMappingError
from ..domain.enums import UserRole
from .orm_models import AnalysisTaskORM, ArtifactORM, AuditEventORM, AuthSessionORM, DatasetORM, ExecutionORM, ReportORM, TaskEventORM, ToolCallORM, UserORM
from .models import AnalysisTaskRecord, ArtifactRecord, AuditEventRecord, AuthSessionRecord, DatasetRecord, ExecutionResultRecord, ReportRecord, TaskEventRecord, ToolCallRecord, UserRecord


ARTIFACT_PERSISTENCE_NAMESPACE = "__data_analysis_agent_persistence__"
ARTIFACT_TITLE_METADATA_KEY = "artifact_title"
_MISSING = object()


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def user_orm_to_record(row: UserORM) -> UserRecord:
    role = row.role.value if hasattr(row.role, "value") else row.role
    role = role or UserRole.USER
    is_active = True if row.is_active is None else row.is_active
    return UserRecord(
        user_id=_uuid(row.user_id),
        created_at=row.created_at,
        email_normalized=row.email_normalized,
        password_hash=row.password_hash,
        role=role,
        is_active=is_active,
    )


def auth_session_orm_to_record(row: AuthSessionORM) -> AuthSessionRecord:
    return AuthSessionRecord(
        session_id=_uuid(row.session_id),
        token_hash=row.token_hash,
        user_id=_uuid(row.user_id),
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
    )


def audit_event_orm_to_record(row: AuditEventORM) -> AuditEventRecord:
    action = row.action.value if hasattr(row.action, "value") else row.action
    return AuditEventRecord(
        event_id=_uuid(row.event_id),
        user_id=_uuid(row.user_id) if row.user_id else None,
        action=action,
        target_type=row.target_type,
        target_id=_uuid(row.target_id) if row.target_id else None,
        success=row.success,
        request_id=row.request_id,
        occurred_at=row.occurred_at,
        metadata_json=deepcopy(row.metadata_json or {}),
    )


def dataset_orm_to_record(row: DatasetORM) -> DatasetRecord:
    return DatasetRecord(dataset_id=_uuid(row.dataset_id), user_id=_uuid(row.user_id), name=row.name, source_uri=row.source_uri, content_type=row.content_type, size_bytes=row.size_bytes or 0, checksum=row.checksum, created_at=row.created_at, metadata_json=deepcopy(row.metadata_json or {}))


def task_orm_to_record(row: AnalysisTaskORM, dataset_ids: list[UUID]) -> AnalysisTaskRecord:
    return AnalysisTaskRecord(task_id=_uuid(row.task_id), user_id=_uuid(row.user_id), idempotency_key=row.idempotency_key, request_hash=row.request_hash, query=row.query, dataset_ids_json=[str(_uuid(i)) for i in dataset_ids], status=row.status.value if hasattr(row.status, "value") else row.status, max_rounds=row.max_rounds, created_at=row.created_at, updated_at=row.updated_at, error_code=row.error_code, error_message=row.error_message, metadata_json=deepcopy(row.metadata_json or {}), model_call_count=row.model_call_count or 0, model_duration_ms=row.model_duration_ms or 0)


def event_orm_to_record(row: TaskEventORM) -> TaskEventRecord:
    value = lambda x: x.value if hasattr(x, "value") else x
    return TaskEventRecord(event_id=_uuid(row.event_id), task_id=_uuid(row.task_id), event_type=value(row.event_type), from_status=value(row.from_status), to_status=value(row.to_status), message=row.message, occurred_at=row.occurred_at, metadata_json=deepcopy(row.metadata_json or {}))


def tool_call_orm_to_record(row: ToolCallORM) -> ToolCallRecord:
    value = lambda x: x.value if hasattr(x, "value") else x
    return ToolCallRecord(tool_call_id=_uuid(row.tool_call_id), task_id=_uuid(row.task_id), tool_name=row.tool_name, arguments_json=deepcopy(row.arguments_json or {}), result_json=deepcopy(row.result_json), status=value(row.status), started_at=row.started_at, finished_at=row.finished_at, error_message=row.error_message)


def execution_orm_to_record(row: ExecutionORM) -> ExecutionResultRecord:
    return ExecutionResultRecord(execution_result_id=_uuid(row.execution_result_id), tool_call_id=_uuid(row.tool_call_id) if row.tool_call_id else None, success=row.success, output_text=row.output_text, error_text=row.error_text, variables_json=deepcopy(row.variables_json or {}), duration_ms=row.duration_ms)


def artifact_orm_to_record(row: ArtifactORM) -> ArtifactRecord:
    metadata_json, title = _artifact_metadata_from_storage(row.metadata_json)
    return ArtifactRecord(artifact_id=_uuid(row.artifact_id), task_id=_uuid(row.task_id), artifact_type=row.artifact_type, name=row.name, file_path=row.file_path, format=row.format.value if hasattr(row.format, "value") else row.format, mime_type=row.mime_type, content_hash=row.content_hash, size_bytes=row.size_bytes or 0, description=row.description, title=title, source_tool_call_id=_uuid(row.source_tool_call_id) if row.source_tool_call_id else None, metadata_json=metadata_json, created_at=row.created_at)


def report_orm_to_record(row: ReportORM) -> ReportRecord:
    return ReportRecord(report_id=_uuid(row.report_id), artifact_id=_uuid(row.artifact_id), task_id=_uuid(row.task_id), format=row.format.value if hasattr(row.format, "value") else row.format, storage_uri=row.storage_uri, size_bytes=row.size_bytes or 0, content_hash=row.content_hash, created_at=row.created_at)


def user_record_to_orm(record: UserRecord) -> UserORM:
    return UserORM(
        user_id=_uuid(record.user_id),
        created_at=record.created_at,
        email_normalized=record.email_normalized,
        password_hash=record.password_hash,
        role=record.role,
        is_active=record.is_active,
    )


def auth_session_record_to_orm(record: AuthSessionRecord) -> AuthSessionORM:
    return AuthSessionORM(
        session_id=_uuid(record.session_id),
        token_hash=record.token_hash,
        user_id=_uuid(record.user_id),
        created_at=record.created_at,
        expires_at=record.expires_at,
        last_seen_at=record.last_seen_at,
        revoked_at=record.revoked_at,
    )


def audit_event_record_to_orm(record: AuditEventRecord) -> AuditEventORM:
    return AuditEventORM(
        event_id=_uuid(record.event_id),
        user_id=_uuid(record.user_id) if record.user_id else None,
        action=record.action,
        target_type=record.target_type,
        target_id=_uuid(record.target_id) if record.target_id else None,
        success=record.success,
        request_id=record.request_id,
        occurred_at=record.occurred_at,
        metadata_json=deepcopy(record.metadata_json),
    )


def dataset_record_to_orm(record: DatasetRecord) -> DatasetORM:
    return DatasetORM(user_id=_uuid(record.user_id), dataset_id=_uuid(record.dataset_id), name=record.name, source_uri=record.source_uri, content_type=record.content_type, size_bytes=record.size_bytes, checksum=record.checksum, created_at=record.created_at, metadata_json=deepcopy(record.metadata_json))


def task_record_to_orm(record: AnalysisTaskRecord) -> AnalysisTaskORM:
    return AnalysisTaskORM(task_id=_uuid(record.task_id), user_id=_uuid(record.user_id), idempotency_key=record.idempotency_key, request_hash=record.request_hash, query=record.query, status=record.status, max_rounds=record.max_rounds, created_at=record.created_at, updated_at=record.updated_at, error_code=record.error_code, error_message=record.error_message, metadata_json=deepcopy(record.metadata_json), model_call_count=record.model_call_count, model_duration_ms=record.model_duration_ms)


def event_record_to_orm(record: TaskEventRecord) -> TaskEventORM:
    return TaskEventORM(event_id=_uuid(record.event_id), task_id=_uuid(record.task_id), event_type=record.event_type, from_status=record.from_status, to_status=record.to_status, message=record.message, occurred_at=record.occurred_at, metadata_json=deepcopy(record.metadata_json))


def tool_call_record_to_orm(record: ToolCallRecord) -> ToolCallORM:
    return ToolCallORM(tool_call_id=_uuid(record.tool_call_id), task_id=_uuid(record.task_id), tool_name=record.tool_name, arguments_json=deepcopy(record.arguments_json), result_json=deepcopy(record.result_json), status=record.status, started_at=record.started_at, finished_at=record.finished_at, error_message=record.error_message)


def execution_record_to_orm(record: ExecutionResultRecord) -> ExecutionORM:
    return ExecutionORM(execution_result_id=_uuid(record.execution_result_id), tool_call_id=_uuid(record.tool_call_id) if record.tool_call_id else None, success=record.success, output_text=record.output_text, error_text=record.error_text, variables_json=deepcopy(record.variables_json), duration_ms=record.duration_ms)


def artifact_record_to_orm(record: ArtifactRecord) -> ArtifactORM:
    return ArtifactORM(artifact_id=_uuid(record.artifact_id), task_id=_uuid(record.task_id), artifact_type=record.artifact_type, name=record.name, file_path=record.file_path, format=record.format, mime_type=record.mime_type, content_hash=record.content_hash, size_bytes=record.size_bytes, description=record.description, source_tool_call_id=_uuid(record.source_tool_call_id) if record.source_tool_call_id else None, metadata_json=_artifact_metadata_for_storage(record.metadata_json, record.title), created_at=record.created_at)


def report_record_to_orm(record: ReportRecord) -> ReportORM:
    return ReportORM(report_id=_uuid(record.report_id), artifact_id=_uuid(record.artifact_id), task_id=_uuid(record.task_id), format=record.format, storage_uri=record.storage_uri, size_bytes=record.size_bytes, content_hash=record.content_hash, created_at=record.created_at)


def _artifact_metadata_for_storage(metadata_json: Mapping, title: str | None) -> dict:
    if not isinstance(metadata_json, Mapping):
        raise PersistenceMappingError("artifact metadata must be a mapping")
    if ARTIFACT_PERSISTENCE_NAMESPACE in metadata_json:
        raise PersistenceMappingError(
            "artifact metadata uses the reserved persistence namespace"
        )
    metadata = deepcopy(dict(metadata_json))
    if title is not None:
        metadata[ARTIFACT_PERSISTENCE_NAMESPACE] = {
            ARTIFACT_TITLE_METADATA_KEY: title,
        }
    return metadata


def _artifact_metadata_from_storage(metadata_json: Mapping | None) -> tuple[dict, str | None]:
    if metadata_json is not None and not isinstance(metadata_json, Mapping):
        raise PersistenceMappingError("artifact metadata must be a mapping")
    metadata = deepcopy(dict(metadata_json or {}))
    persistence_values = metadata.pop(ARTIFACT_PERSISTENCE_NAMESPACE, _MISSING)
    if persistence_values is _MISSING:
        return metadata, None
    if not isinstance(persistence_values, Mapping):
        raise PersistenceMappingError(
            "invalid artifact persistence metadata namespace"
        )
    if set(persistence_values) != {ARTIFACT_TITLE_METADATA_KEY}:
        raise PersistenceMappingError(
            "invalid artifact persistence metadata namespace"
        )
    title = persistence_values[ARTIFACT_TITLE_METADATA_KEY]
    if not isinstance(title, str):
        raise PersistenceMappingError("invalid artifact title metadata")
    return metadata, title
