from io import BytesIO
from uuid import uuid4

import pytest

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.resolver import DatasetResolver
from data_analysis_agent.datasets.service import DatasetUploadService, UnitOfWorkDatasetStore
from data_analysis_agent.datasets.storage import LocalStorageBackend
from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.unit_of_work import UnitOfWork


@pytest.fixture
def uow_factory(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'datasets.sqlite3'}",
        },
    )
    database = Database.from_settings(settings)
    init_database(database.engine)
    try:
        yield lambda: UnitOfWork(database.session_factory)
    finally:
        database.engine.dispose()


@pytest.fixture
def uploaded_dataset(tmp_path, uow_factory):
    storage = LocalStorageBackend(tmp_path / "objects")
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )
    owner_id = uuid4()
    result = service.upload(
        BytesIO(b"name,value\nA,1\n"),
        original_filename="sales.csv",
        owner_id=owner_id,
    )
    return DatasetResolver(storage=storage, metadata_store=UnitOfWorkDatasetStore(uow_factory)), result.dataset_id, owner_id
