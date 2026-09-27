from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config.settings import Settings, load_settings
from ..persistence.database import Database
from ..persistence.unit_of_work import UnitOfWork
from ..datasets import CsvInspector, DatasetCatalogService, DatasetUploadService, UnitOfWorkDatasetStore
from ..storage.factory import build_storage
from ..storage import Storage
from .auth import PrincipalProvider


@dataclass
class APIApplication:
    """Dependencies shared by API routes for one application instance."""

    settings: Settings = field(default_factory=load_settings)
    database: Any = None
    storage: Storage | None = None
    dataset_upload: Any = None
    dataset_catalog: Any = None
    task_persistence: Any = None
    task_submission: Any = None
    file_access: Any = None
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
