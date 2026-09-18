from io import BytesIO
from uuid import uuid4

from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.service import DatasetUploadService, UnitOfWorkDatasetStore
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_upload_acceptance_returns_preview_schema_counts_and_missing_values(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    metadata_store = UnitOfWorkDatasetStore(uow_factory)
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=metadata_store,
        max_upload_size=4096,
    )

    original_csv = b"name,value\nA,1\nB,\n"
    result = service.upload(
        BytesIO(original_csv),
        original_filename="sample.csv",
        owner_id=owner_id,
    )

    assert result.profile.row_count == 2
    assert [column.name for column in result.profile.columns] == ["name", "value"]
    assert next(column for column in result.profile.columns if column.name == "value").missing_count == 1
    assert len(result.profile.preview_rows) == 2
    assert result.dataset_id

    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)

    assert record is not None
    assert record.source_uri
    assert record.metadata_json["profile"] == result.profile.model_dump(mode="json")
    with storage.open(record.source_uri) as stored_object:
        assert stored_object.read() == original_csv
