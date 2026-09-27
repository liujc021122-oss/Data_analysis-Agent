from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID
from ..config.settings import Settings, load_settings
from ..datasets import DatasetCatalogService, DatasetUploadService
from ..persistence.database import Database
from ..persistence.models import ArtifactRecord
from ..persistence.unit_of_work import UnitOfWork
from ..datasets import CsvInspector, UnitOfWorkDatasetStore
from ..storage.factory import build_storage
from ..storage import FileAccessService, Storage

if TYPE_CHECKING:
    from ..services.persistence import TaskPersistenceService
    from ..worker import TaskSubmissionService
    from ..worker.broker import TaskBroker
from .auth import PrincipalProvider


class _AuthorizedArtifactLookup:
    """Open a fresh unit of work for each owner-scoped artifact lookup."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_for_user(self, artifact_id: UUID, user_id: UUID) -> ArtifactRecord | None:
        with UnitOfWork(self._database.session_factory) as uow:
            return uow.artifacts.get_for_user(artifact_id, user_id)


@dataclass
class APIApplication:
    """Dependencies shared by API routes for one application instance."""

    settings: Settings = field(default_factory=load_settings)
    database: Database | None = None
    storage: Storage | None = None
    dataset_upload: DatasetUploadService | None = None
    dataset_catalog: DatasetCatalogService | None = None
    task_persistence: TaskPersistenceService | None = None
    task_submission: TaskSubmissionService | None = None
    file_access: FileAccessService | None = None
    principal_provider: PrincipalProvider | None = None

    def configure_task_services(self, broker: TaskBroker) -> None:
        if self.database is None:
            raise ValueError("DATABASE_URL is required for task services")
        from ..services.persistence import TaskPersistenceService
        from ..worker.service import TaskSubmissionService

        self.task_persistence = TaskPersistenceService(
            lambda: UnitOfWork(self.database.session_factory)
        )
        self.task_submission = TaskSubmissionService(
            persistence=self.task_persistence, broker=broker
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "APIApplication":
        database = Database.from_settings(settings) if settings.database_url else None
        storage = build_storage(settings)
        if database is None:
            return cls(settings=settings, database=None, storage=storage)
        def uow_factory():
            return UnitOfWork(database.session_factory)
        metadata_store = UnitOfWorkDatasetStore(uow_factory)
        application = cls(
            settings=settings,
            database=database,
            storage=storage,
            dataset_upload=DatasetUploadService(storage=storage, inspector=CsvInspector(), metadata_store=metadata_store, max_upload_size=settings.max_upload_size),
            dataset_catalog=DatasetCatalogService(storage=storage, uow_factory=uow_factory),
            file_access=FileAccessService(
                storage=storage,
                artifact_repository=_AuthorizedArtifactLookup(database),
            ),
        )
        if settings.app_env == "production" or (
            settings.app_env == "development" and settings.redis_url
        ):
            from ..worker.celery_app import build_celery_broker

            application.configure_task_services(build_celery_broker(settings))
        elif settings.app_env in {"development", "test"}:
            from ..worker.broker import InMemoryTaskBroker

            application.configure_task_services(InMemoryTaskBroker())
        return application
