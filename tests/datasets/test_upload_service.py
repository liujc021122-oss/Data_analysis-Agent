from io import BytesIO
from uuid import uuid4

import pytest

from data_analysis_agent.datasets.errors import UploadValidationError
from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.service import DatasetUploadService, UnitOfWorkDatasetStore
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_upload_persists_only_metadata_and_returns_opaque_dataset_id(tmp_path, uow_factory):
    owner_id = uuid4()
    service = DatasetUploadService(
        storage=LocalStorageBackend(tmp_path / "objects"),
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )

    result = service.upload(
        BytesIO("name,value\nA,1\n".encode()),
        original_filename="../../sales.csv",
        owner_id=owner_id,
    )

    assert result.dataset_id
    assert result.original_filename == "sales.csv"
    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)
        assert record is not None
        assert record.source_uri.startswith("local://")
        assert record.metadata_json["profile"]["row_count"] == 1


def test_invalid_upload_does_not_create_metadata_or_object(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )

    with pytest.raises(UploadValidationError):
        service.upload(BytesIO(b"a,a\n1,2\n"), original_filename="bad.csv", owner_id=owner_id)

    with uow_factory() as uow:
        assert uow.datasets.list_for_user(owner_id) == []
    assert list((tmp_path / "objects").rglob("*")) == []
