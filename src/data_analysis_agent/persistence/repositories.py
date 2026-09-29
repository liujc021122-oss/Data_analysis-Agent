from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from ..domain.errors import PersistenceMappingError
from ..domain.enums import ReportFormat, TaskStatus, ToolCallStatus
from ..domain.models import AnalysisTask, ExecutionResult, TaskEvent, ToolCall, utc_now
from ..services.authorization import AccessSubject
from .errors import EntityNotFoundError
from .errors import IdempotencyConflictError
from .mappers import _normalize_json_value, event_to_record, record_to_event, record_to_task, task_to_record
from .models import (
    ArtifactRecord, AuditEventRecord, AuthSessionRecord, DatasetRecord,
    ExecutionResultRecord, ReportRecord, ToolCallRecord, UserRecord,
)
from .orm_mappers import (
    audit_event_orm_to_record,
    audit_event_record_to_orm,
    auth_session_orm_to_record,
    auth_session_record_to_orm,
    dataset_orm_to_record,
    event_orm_to_record,
    task_orm_to_record,
    user_orm_to_record,
    dataset_record_to_orm,
    event_record_to_orm,
    task_record_to_orm,
    user_record_to_orm,
    artifact_orm_to_record, execution_orm_to_record, report_orm_to_record,
    tool_call_orm_to_record, artifact_record_to_orm, execution_record_to_orm,
    report_record_to_orm, tool_call_record_to_orm,
)
from .orm_models import (
    AnalysisTaskORM, ArtifactORM, AuditEventORM, AuthSessionORM, DatasetORM,
    ExecutionORM, ReportORM, TaskEventORM, ToolCallORM, UserORM,
    task_dataset_link,
)
class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure(self, record: UserRecord) -> UserRecord:
        row = self.session.get(UserORM, record.user_id)
        if row is None:
            row = user_record_to_orm(record)
            self.session.add(row)
            self.session.flush()
        return user_orm_to_record(row)

    def get(self, user_id: UUID) -> UserRecord | None:
        row = self.session.get(UserORM, user_id)
        return user_orm_to_record(row) if row else None

    def get_by_email(self, email_normalized: str) -> UserRecord | None:
        row = self.session.scalar(
            select(UserORM).where(UserORM.email_normalized == email_normalized)
        )
        return user_orm_to_record(row) if row else None


class AuthSessionRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: AuthSessionRecord) -> AuthSessionRecord:
        row = auth_session_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return auth_session_orm_to_record(row)

    def get_active_by_token_hash(
        self, token_hash: str, now: datetime
    ) -> AuthSessionRecord | None:
        row = self.session.scalar(
            select(AuthSessionORM)
            .join(UserORM, UserORM.user_id == AuthSessionORM.user_id)
            .where(
                AuthSessionORM.token_hash == token_hash,
                AuthSessionORM.revoked_at.is_(None),
                AuthSessionORM.expires_at > now,
                UserORM.is_active.is_(True),
            )
        )
        return auth_session_orm_to_record(row) if row else None

    def touch(self, session_id: UUID, last_seen_at: datetime) -> bool:
        result = self.session.execute(
            update(AuthSessionORM)
            .where(
                AuthSessionORM.session_id == session_id,
                AuthSessionORM.revoked_at.is_(None),
            )
            .values(last_seen_at=last_seen_at)
        )
        self.session.flush()
        return result.rowcount == 1

    def revoke(self, session_id: UUID, revoked_at: datetime) -> bool:
        result = self.session.execute(
            update(AuthSessionORM)
            .where(
                AuthSessionORM.session_id == session_id,
                AuthSessionORM.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        self.session.flush()
        return result.rowcount == 1


class AuditEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: AuditEventRecord) -> AuditEventRecord:
        row = audit_event_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return audit_event_orm_to_record(row)


class DatasetRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: DatasetRecord) -> DatasetRecord:
        row = dataset_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return dataset_orm_to_record(row)

    def get(self, dataset_id: UUID) -> DatasetRecord | None:
        row = self.session.get(DatasetORM, dataset_id)
        return dataset_orm_to_record(row) if row else None

    def get_for_user(self, dataset_id: UUID, user_id: UUID) -> DatasetRecord | None:
        row = self.session.scalar(
            select(DatasetORM).where(
                DatasetORM.dataset_id == dataset_id,
                DatasetORM.user_id == user_id,
            )
        )
        return dataset_orm_to_record(row) if row else None

    def get_for_subject(
        self, dataset_id: UUID, subject: AccessSubject
    ) -> DatasetRecord | None:
        if subject.is_admin:
            return self.get(dataset_id)
        return self.get_for_user(dataset_id, subject.user_id)

    def count_for_user(self, user_id: UUID) -> int:
        from sqlalchemy import func
        return int(self.session.scalar(select(func.count()).select_from(DatasetORM).where(DatasetORM.user_id == user_id)) or 0)

    def count_for_subject(self, subject: AccessSubject) -> int:
        if subject.is_admin:
            return int(self.session.scalar(select(func.count()).select_from(DatasetORM)) or 0)
        return self.count_for_user(subject.user_id)

    def list_for_user(self, user_id: UUID, *, offset: int = 0, limit: int | None = None) -> list[DatasetRecord]:
        statement = select(DatasetORM).where(DatasetORM.user_id == user_id).order_by(DatasetORM.created_at, DatasetORM.dataset_id).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return [dataset_orm_to_record(row) for row in rows]

    def list_for_subject(
        self, subject: AccessSubject, *, offset: int = 0, limit: int | None = None
    ) -> list[DatasetRecord]:
        if not subject.is_admin:
            return self.list_for_user(subject.user_id, offset=offset, limit=limit)
        statement = select(DatasetORM).order_by(
            DatasetORM.created_at, DatasetORM.dataset_id
        ).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return [dataset_orm_to_record(row) for row in rows]

    def delete_for_user(self, dataset_id: UUID, user_id: UUID) -> bool:
        row = self.session.scalar(select(DatasetORM).where(DatasetORM.dataset_id == dataset_id, DatasetORM.user_id == user_id))
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def delete_for_subject(self, dataset_id: UUID, subject: AccessSubject) -> bool:
        if subject.is_admin:
            row = self.session.get(DatasetORM, dataset_id)
            if row is None:
                return False
            self.session.delete(row)
            self.session.flush()
            return True
        return self.delete_for_user(dataset_id, subject.user_id)


@dataclass(frozen=True)
class TaskCreationResult:
    task: AnalysisTask
    created: bool


class TaskRepository:
    _IDEMPOTENCY_CONSTRAINT_NAME = "uq_analysis_tasks_user_idempotency"

    def __init__(self, session: Session):
        self.session = session

    def add(self, *, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str) -> AnalysisTask:
        record = task_to_record(task).model_copy(update={"user_id": user_id, "idempotency_key": idempotency_key, "request_hash": request_hash})
        row = task_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return self._to_domain(row)

    def create_idempotent_with_result(
        self, *, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str
    ) -> TaskCreationResult:
        existing = self._get_by_idempotency(user_id, idempotency_key)
        if existing is not None:
            existing_task, existing_hash = existing
            return self._check_idempotency(existing_task, existing_hash, request_hash)

        record = task_to_record(task).model_copy(
            update={
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            }
        )
        row = task_record_to_orm(record)
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            if not self._is_idempotency_conflict(exc):
                raise
            winner = self._get_by_idempotency(user_id, idempotency_key)
            if winner is None:
                raise
            winner_task, winner_hash = winner
            return self._check_idempotency(winner_task, winner_hash, request_hash)
        return TaskCreationResult(task, True)

    def create_idempotent(
        self, *, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str
    ) -> AnalysisTask:
        return self.create_idempotent_with_result(
            user_id=user_id,
            task=task,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        ).task

    def record_model_call(self, *, task_id: UUID, duration_ms: int) -> AnalysisTask:
        if duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        statement = (
            update(AnalysisTaskORM)
            .where(AnalysisTaskORM.task_id == task_id)
            .values(
                model_call_count=AnalysisTaskORM.model_call_count + 1,
                model_duration_ms=AnalysisTaskORM.model_duration_ms + duration_ms,
                updated_at=utc_now(),
            )
        )
        result = self.session.execute(statement)
        if result.rowcount != 1:
            raise EntityNotFoundError(f"task {task_id} not found")
        self.session.flush()
        restored = self.get(task_id)
        if restored is None:
            raise EntityNotFoundError(f"task {task_id} not found")
        return restored

    def _get_by_idempotency(
        self, user_id: UUID, idempotency_key: str
    ) -> tuple[AnalysisTask, str] | None:
        row = self.session.scalars(
            select(AnalysisTaskORM).where(
                AnalysisTaskORM.user_id == user_id,
                AnalysisTaskORM.idempotency_key == idempotency_key,
            )
        ).first()
        return (self._to_domain(row), row.request_hash) if row else None

    def get_by_idempotency(
        self, user_id: UUID, idempotency_key: str
    ) -> AnalysisTask | None:
        existing = self._get_by_idempotency(user_id, idempotency_key)
        return existing[0] if existing is not None else None

    def get_owner_id(self, task_id: UUID) -> UUID | None:
        row = self.session.get(AnalysisTaskORM, task_id)
        return row.user_id if row is not None else None

    def claim_queued(self, task_id: UUID) -> AnalysisTask | None:
        """Atomically move a queued task to RUNNING when this worker wins."""
        statement = (
            update(AnalysisTaskORM)
            .where(
                AnalysisTaskORM.task_id == task_id,
                AnalysisTaskORM.status == TaskStatus.QUEUED,
            )
            .values(status=TaskStatus.RUNNING, updated_at=utc_now())
        )
        result = self.session.execute(statement)
        if result.rowcount != 1:
            return None
        self.session.flush()
        return self.get(task_id)

    def list_status_before(
        self, *, status, before: datetime
    ) -> list[AnalysisTask]:
        rows = self.session.scalars(
            select(AnalysisTaskORM)
            .where(
                AnalysisTaskORM.status == status,
                AnalysisTaskORM.updated_at < before,
            )
            .order_by(AnalysisTaskORM.updated_at, AnalysisTaskORM.task_id)
        ).all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _check_idempotency(
        task: AnalysisTask, existing_hash: str, request_hash: str
    ) -> TaskCreationResult:
        if existing_hash != request_hash:
            raise IdempotencyConflictError("idempotency key was reused for a different request")
        return TaskCreationResult(task, False)

    @staticmethod
    def _is_idempotency_conflict(exc: IntegrityError) -> bool:
        original = exc.orig
        diagnostic = getattr(original, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name is None:
            constraint_name = getattr(original, "constraint_name", None)
        if constraint_name is not None:
            return constraint_name == TaskRepository._IDEMPOTENCY_CONSTRAINT_NAME

        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(TaskRepository._IDEMPOTENCY_CONSTRAINT_NAME)}(?![A-Za-z0-9_])",
            str(original),
        ):
            return True

        # SQLite does not include a named constraint in its error message.
        # Its unique-constraint error code plus the exact qualified columns
        # derived from the named ORM constraint is the dialect-specific check.
        if getattr(original, "sqlite_errorname", None) != "SQLITE_CONSTRAINT_UNIQUE":
            return False
        constraint = next(
            constraint
            for constraint in AnalysisTaskORM.__table__.constraints
            if constraint.name == TaskRepository._IDEMPOTENCY_CONSTRAINT_NAME
        )
        columns = ", ".join(
            f"{AnalysisTaskORM.__table__.name}.{column.name}"
            for column in constraint.columns
        )
        return str(original) == f"UNIQUE constraint failed: {columns}"

    def get(self, task_id: UUID) -> AnalysisTask | None:
        return self._read(task_id, for_update=False)

    def get_for_update(self, task_id: UUID) -> AnalysisTask | None:
        return self._read(task_id, for_update=True)

    def get_for_user(self, task_id: UUID, user_id: UUID, *, for_update: bool = False) -> AnalysisTask | None:
        query = select(AnalysisTaskORM).where(
            AnalysisTaskORM.task_id == task_id, AnalysisTaskORM.user_id == user_id
        )
        if for_update:
            query = query.with_for_update()
        row = self.session.scalar(query)
        return self._to_domain(row) if row is not None else None

    def get_for_subject(
        self, task_id: UUID, subject: AccessSubject, *, for_update: bool = False
    ) -> AnalysisTask | None:
        if subject.is_admin:
            return self.get_for_update(task_id) if for_update else self.get(task_id)
        return self.get_for_user(task_id, subject.user_id, for_update=for_update)

    def list_for_user(
        self, user_id: UUID, *, status: TaskStatus | None = None,
        offset: int = 0, limit: int = 20,
    ) -> tuple[list[AnalysisTask], int]:
        condition = [AnalysisTaskORM.user_id == user_id]
        if status is not None:
            condition.append(AnalysisTaskORM.status == status)
        total = int(self.session.scalar(
            select(func.count()).select_from(AnalysisTaskORM).where(*condition)
        ) or 0)
        rows = self.session.scalars(
            select(AnalysisTaskORM).where(*condition)
            .order_by(AnalysisTaskORM.created_at, AnalysisTaskORM.task_id)
            .offset(offset).limit(limit)
        ).all()
        return [self._to_domain(row) for row in rows], total

    def list_for_subject(
        self,
        subject: AccessSubject,
        *,
        status: TaskStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AnalysisTask], int]:
        if not subject.is_admin:
            return self.list_for_user(
                subject.user_id, status=status, offset=offset, limit=limit
            )
        conditions = []
        if status is not None:
            conditions.append(AnalysisTaskORM.status == status)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(AnalysisTaskORM).where(*conditions)
            )
            or 0
        )
        rows = self.session.scalars(
            select(AnalysisTaskORM)
            .where(*conditions)
            .order_by(AnalysisTaskORM.created_at, AnalysisTaskORM.task_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._to_domain(row) for row in rows], total

    def update(self, task: AnalysisTask) -> AnalysisTask:
        row = self.session.get(AnalysisTaskORM, task.task_id)
        if row is None:
            raise EntityNotFoundError(f"task {task.task_id} not found")
        record = task_to_record(task)
        row.query = record.query
        row.status = record.status
        row.max_rounds = record.max_rounds
        row.created_at = record.created_at
        row.updated_at = record.updated_at
        row.error_code = record.error_code
        row.error_message = record.error_message
        row.metadata_json = record.metadata_json
        row.model_call_count = record.model_call_count
        row.model_duration_ms = record.model_duration_ms
        self.session.flush()
        return self._to_domain(row)

    def update_if_status(self, task: AnalysisTask, *, expected: TaskStatus) -> bool:
        """Atomically claim a status transition, including on SQLite."""
        record = task_to_record(task)
        result = self.session.execute(
            update(AnalysisTaskORM)
            .where(AnalysisTaskORM.task_id == task.task_id,
                   AnalysisTaskORM.status == expected)
            .values(
                status=record.status, updated_at=record.updated_at,
                error_code=record.error_code, error_message=record.error_message,
                metadata_json=record.metadata_json,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def attach_dataset(
        self, *, task_id: UUID, dataset_id: UUID, position: int | None = None
    ) -> None:
        if self.session.get(AnalysisTaskORM, task_id) is None:
            raise EntityNotFoundError(f"task {task_id} not found")
        if self.session.get(DatasetORM, dataset_id) is None:
            raise EntityNotFoundError(f"dataset {dataset_id} not found")
        if position is None:
            last_position = self.session.scalar(
                select(func.max(task_dataset_link.c.position)).where(
                    task_dataset_link.c.task_id == task_id
                )
            )
            position = 0 if last_position is None else last_position + 1
        if position < 0:
            raise ValueError("position must be non-negative")
        self.session.execute(
            task_dataset_link.insert().values(
                task_id=task_id, dataset_id=dataset_id, position=position
            )
        )
        self.session.flush()

    def _read(self, task_id: UUID, *, for_update: bool) -> AnalysisTask | None:
        query = select(AnalysisTaskORM).where(AnalysisTaskORM.task_id == task_id)
        if for_update:
            query = query.with_for_update()
        try:
            with self.session.no_autoflush:
                row = self.session.scalars(query).first()
        except StatementError as exc:
            if isinstance(exc.orig, LookupError):
                raise PersistenceMappingError("Invalid status in persistence record") from exc
            raise
        return self._to_domain(row) if row else None

    def _to_domain(self, row: AnalysisTaskORM) -> AnalysisTask:
        dataset_ids = list(
            self.session.execute(
                select(task_dataset_link.c.dataset_id)
                .where(task_dataset_link.c.task_id == row.task_id)
                .order_by(task_dataset_link.c.position)
            ).scalars()
        )
        try:
            return record_to_task(task_orm_to_record(row, dataset_ids))
        except LookupError as exc:
            raise PersistenceMappingError("Invalid status in persistence record") from exc


class TaskEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def append(self, event: TaskEvent) -> TaskEvent:
        row = event_record_to_orm(event_to_record(event))
        self.session.add(row)
        self.session.flush()
        return record_to_event(event_orm_to_record(row))

    def list_for_task(self, task_id: UUID) -> list[TaskEvent]:
        try:
            rows = self.session.scalars(select(TaskEventORM).where(TaskEventORM.task_id == task_id).order_by(TaskEventORM.occurred_at, TaskEventORM.event_id)).all()
        except StatementError as exc:
            if isinstance(exc.orig, LookupError):
                raise PersistenceMappingError("Invalid event field in persistence record") from exc
            raise
        try:
            return [record_to_event(event_orm_to_record(row)) for row in rows]
        except LookupError as exc:
            raise PersistenceMappingError("Invalid event field in persistence record") from exc

    def page_for_task(self, task_id: UUID, *, offset: int, limit: int) -> tuple[list[TaskEvent], int]:
        total = int(self.session.scalar(
            select(func.count()).select_from(TaskEventORM).where(TaskEventORM.task_id == task_id)
        ) or 0)
        rows = self.session.scalars(
            select(TaskEventORM).where(TaskEventORM.task_id == task_id)
            .order_by(TaskEventORM.occurred_at, TaskEventORM.event_id)
            .offset(offset).limit(limit)
        ).all()
        return [record_to_event(event_orm_to_record(row)) for row in rows], total

    def latest_occurred_at(self, task_id: UUID):
        return self.session.scalar(
            select(func.max(TaskEventORM.occurred_at)).where(
                TaskEventORM.task_id == task_id
            )
        )


class ToolCallRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, call: ToolCall) -> ToolCall:
        row = tool_call_record_to_orm(ToolCallRecord(
            tool_call_id=call.tool_call_id, task_id=call.task_id, tool_name=call.tool_name,
            arguments_json=_normalize_json_value(call.arguments), result_json=_normalize_json_value(call.result),
            status=call.status.value, started_at=call.started_at, finished_at=call.finished_at,
            error_message=call.error_message,
        ))
        self.session.add(row)
        self.session.flush()
        return self._to_domain(row)

    def get(self, tool_call_id: UUID) -> ToolCall | None:
        row = self._row(tool_call_id)
        return self._to_domain(row) if row else None

    def list_for_task(self, task_id: UUID) -> list[ToolCall]:
        try:
            rows = self.session.scalars(select(ToolCallORM).where(ToolCallORM.task_id == task_id).order_by(ToolCallORM.started_at, ToolCallORM.tool_call_id)).all()
        except (LookupError, StatementError) as exc:
            raise PersistenceMappingError("Invalid tool call status in persistence record") from exc
        return [self._to_domain(row) for row in rows]

    def _row(self, tool_call_id):
        try:
            return self.session.get(ToolCallORM, tool_call_id)
        except (LookupError, StatementError) as exc:
            raise PersistenceMappingError("Invalid tool call status in persistence record") from exc

    def _to_domain(self, row):
        try:
            record = tool_call_orm_to_record(row)
            return ToolCall(tool_call_id=record.tool_call_id, task_id=record.task_id,
                            tool_name=record.tool_name, arguments=record.arguments_json,
                            result=record.result_json, status=ToolCallStatus(record.status),
                            started_at=record.started_at, finished_at=record.finished_at,
                            error_message=record.error_message)
        except (LookupError, ValueError, TypeError) as exc:
            raise PersistenceMappingError("Invalid tool call status in persistence record") from exc


class ExecutionRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, execution: ExecutionResult, *, tool_call_id: UUID | None = None) -> ExecutionResult:
        if execution.duration_ms is not None and execution.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        record = ExecutionResultRecord(execution_result_id=uuid4(), tool_call_id=tool_call_id,
            success=execution.success, output_text=execution.output, error_text=execution.error,
            variables_json=_normalize_json_value(execution.variables), duration_ms=execution.duration_ms)
        row = execution_record_to_orm(record)
        row.created_at = utc_now()
        self.session.add(row)
        self.session.flush()
        return self._to_domain(row)

    def list_for_tool_call(self, tool_call_id: UUID) -> list[ExecutionResult]:
        rows = self.session.scalars(select(ExecutionORM).where(ExecutionORM.tool_call_id == tool_call_id).order_by(ExecutionORM.created_at, ExecutionORM.execution_result_id)).all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row):
        record = execution_orm_to_record(row)
        return ExecutionResult(success=record.success, output=record.output_text,
                               error=record.error_text, variables=record.variables_json,
                               duration_ms=record.duration_ms)


class ArtifactRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        row = artifact_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return self._to_record(row)

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        try:
            row = self.session.get(ArtifactORM, artifact_id)
        except (LookupError, StatementError) as exc:
            raise PersistenceMappingError("Invalid artifact format in persistence record") from exc
        if not row:
            return None
        return self._to_record(row)

    def get_for_user(self, artifact_id: UUID, user_id: UUID) -> ArtifactRecord | None:
        try:
            row = self.session.scalar(
                select(ArtifactORM)
                .join(
                    AnalysisTaskORM,
                    ArtifactORM.task_id == AnalysisTaskORM.task_id,
                )
                .where(
                    ArtifactORM.artifact_id == artifact_id,
                    AnalysisTaskORM.user_id == user_id,
                )
            )
        except (LookupError, StatementError) as exc:
            raise PersistenceMappingError(
                "Invalid artifact format in persistence record"
            ) from exc
        if not row:
            return None
        return self._to_record(row)

    def list_for_task(self, task_id: UUID) -> list[ArtifactRecord]:
        rows = self.session.scalars(select(ArtifactORM).where(ArtifactORM.task_id == task_id).order_by(ArtifactORM.created_at, ArtifactORM.artifact_id)).all()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: ArtifactORM) -> ArtifactRecord:
        try:
            record = artifact_orm_to_record(row)
            if record.format is not None:
                ReportFormat(record.format)
            return record
        except (LookupError, ValueError) as exc:
            raise PersistenceMappingError("Invalid artifact format in persistence record") from exc


class ReportRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: ReportRecord) -> ReportRecord:
        artifact = self.session.scalar(
            select(ArtifactORM).where(
                ArtifactORM.artifact_id == record.artifact_id,
                ArtifactORM.task_id == record.task_id,
            )
        )
        if artifact is None:
            raise EntityNotFoundError(f"artifact {record.artifact_id} not found")
        row = report_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return report_orm_to_record(row)

    def get(self, report_id: UUID) -> ReportRecord | None:
        try:
            row = self.session.get(ReportORM, report_id)
        except (LookupError, StatementError) as exc:
            raise PersistenceMappingError("Invalid report format in persistence record") from exc
        if not row:
            return None
        try:
            record = report_orm_to_record(row)
            ReportFormat(record.format)
            return record
        except (LookupError, ValueError) as exc:
            raise PersistenceMappingError("Invalid report format in persistence record") from exc

    def list_for_task(self, task_id: UUID) -> list[ReportRecord]:
        rows = self.session.scalars(select(ReportORM).where(ReportORM.task_id == task_id).order_by(ReportORM.created_at, ReportORM.report_id)).all()
        return [self.get(row.report_id) for row in rows]
