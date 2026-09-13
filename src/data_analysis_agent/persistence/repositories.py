from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from ..domain.errors import PersistenceMappingError
from ..domain.enums import ReportFormat, ToolCallStatus
from ..domain.models import AnalysisTask, ExecutionResult, TaskEvent, ToolCall, utc_now
from .errors import EntityNotFoundError
from .mappers import _normalize_json_value, event_to_record, record_to_event, record_to_task, task_to_record
from .models import (
    ArtifactRecord, DatasetRecord, ExecutionResultRecord, ReportRecord,
    ToolCallRecord, UserRecord,
)
from .orm_mappers import (
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
    AnalysisTaskORM, ArtifactORM, DatasetORM, ExecutionORM, ReportORM,
    TaskEventORM, ToolCallORM, UserORM, task_dataset_link,
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

    def list_for_user(self, user_id: UUID) -> list[DatasetRecord]:
        rows = self.session.scalars(select(DatasetORM).where(DatasetORM.user_id == user_id).order_by(DatasetORM.created_at, DatasetORM.dataset_id)).all()
        return [dataset_orm_to_record(row) for row in rows]


class TaskRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, *, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str) -> AnalysisTask:
        record = task_to_record(task).model_copy(update={"user_id": user_id, "idempotency_key": idempotency_key, "request_hash": request_hash})
        row = task_record_to_orm(record)
        self.session.add(row)
        self.session.flush()
        return self._to_domain(row)

    def get(self, task_id: UUID) -> AnalysisTask | None:
        return self._read(task_id, for_update=False)

    def get_for_update(self, task_id: UUID) -> AnalysisTask | None:
        return self._read(task_id, for_update=True)

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

    def attach_dataset(self, *, task_id: UUID, dataset_id: UUID) -> None:
        if self.session.get(AnalysisTaskORM, task_id) is None:
            raise EntityNotFoundError(f"task {task_id} not found")
        if self.session.get(DatasetORM, dataset_id) is None:
            raise EntityNotFoundError(f"dataset {dataset_id} not found")
        self.session.execute(task_dataset_link.insert().values(task_id=task_id, dataset_id=dataset_id))
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
                .order_by(task_dataset_link.c.dataset_id)
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
        if self.session.get(ArtifactORM, record.artifact_id) is None:
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
