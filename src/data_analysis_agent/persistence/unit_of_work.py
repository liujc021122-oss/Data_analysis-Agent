from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .errors import TransactionError
from .repositories import DatasetRepository, TaskEventRepository, TaskRepository, UserRepository


class UnitOfWork:
    def __init__(self, session_factory):
        self.session: Session = session_factory()
        self.users = UserRepository(self.session)
        self.datasets = DatasetRepository(self.session)
        self.tasks = TaskRepository(self.session)
        self.task_events = TaskEventRepository(self.session)
        self.tool_calls = None
        self.executions = None
        self.artifacts = None
        self.reports = None

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
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise TransactionError("database transaction commit failed") from exc

    def rollback(self):
        try:
            self.session.rollback()
        except SQLAlchemyError as exc:
            raise TransactionError("database transaction rollback failed") from exc
