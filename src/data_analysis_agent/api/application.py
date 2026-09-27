from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from ..config.settings import Settings, load_settings
from ..datasets import DatasetCatalogService, DatasetUploadService
from ..persistence.database import Database
from ..persistence.unit_of_work import UnitOfWork
from ..datasets import CsvInspector, UnitOfWorkDatasetStore
from ..storage.factory import build_storage
from ..storage import Storage

if TYPE_CHECKING:
    from ..services.persistence import TaskPersistenceService
    from ..storage import FileAccessService
    from ..worker import TaskSubmissionService
from .auth import PrincipalProvider


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

    @classmethod
    def from_settings(cls, settings: Settings) -> "APIApplication":
        database = Database.from_settings(settings) if settings.database_url else None
        storage = build_storage(settings)
        if database is None:
            return cls(settings=settings, database=None, storage=storage)
        def uow_factory():
            return UnitOfWork(database.session_factory)
        metadata_store = UnitOfWorkDatasetStore(uow_factory)
        return cls(
            settings=settings,
            database=database,
            storage=storage,
            dataset_upload=DatasetUploadService(storage=storage, inspector=CsvInspector(), metadata_store=metadata_store, max_upload_size=settings.max_upload_size),
            dataset_catalog=DatasetCatalogService(storage=storage, uow_factory=uow_factory),
        )
