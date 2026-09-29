from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .errors import TransactionError
from .repositories import (
    ArtifactRepository, AuditEventRepository, AuthSessionRepository,
    DatasetRepository, ExecutionRepository, ReportRepository, TaskEventRepository,
    TaskRepository, ToolCallRepository, UserRepository,
)


class UnitOfWork:
    def __init__(self, session_factory):
        self.session: Session = session_factory()
        self.users = UserRepository(self.session)
        self.sessions = AuthSessionRepository(self.session)
        self.audit_events = AuditEventRepository(self.session)
        self.datasets = DatasetRepository(self.session)
        self.tasks = TaskRepository(self.session)
        self.task_events = TaskEventRepository(self.session)
        self.tool_calls = ToolCallRepository(self.session)
        self.executions = ExecutionRepository(self.session)
        self.artifacts = ArtifactRepository(self.session)
        self.reports = ReportRepository(self.session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None:
                self.rollback()
            elif self.session.in_transaction():
                self.commit()
        finally:
            self.session.close()

    def commit(self):
        try:
            self.session.commit()
        except SQLAlchemyError as commit_error:
            try:
                self.session.rollback()
            except SQLAlchemyError as rollback_error:
                error = TransactionError(
                    "database transaction commit failed; rollback failed"
                )
                error.__cause__ = commit_error
                error.__context__ = rollback_error
                raise error
            raise TransactionError("database transaction commit failed") from commit_error

    def rollback(self):
        try:
            self.session.rollback()
        except SQLAlchemyError as exc:
            raise TransactionError("database transaction rollback failed") from exc
