## Task 3: Dataset catalog and dataset Router

**Files:**
- Test: `tests/api/test_datasets.py`
- Modify: `src/data_analysis_agent/datasets/service.py`, `src/data_analysis_agent/datasets/__init__.py`
- Modify: `src/data_analysis_agent/persistence/repositories.py`
- Modify: `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/application.py`
- Modify: `tests/api/conftest.py`, `src/data_analysis_agent/api/routers/datasets.py`
- Create: `src/data_analysis_agent/api/routers/__init__.py`

**Interfaces:**
- `DatasetCatalogService.list_for_user(user_id, offset, limit) -> tuple[list[DatasetRecord], int]`.
- `DatasetCatalogService.get_for_user(user_id, dataset_id) -> DatasetRecord`.
- `DatasetCatalogService.delete_for_user(user_id, dataset_id) -> None`.
- Router paths are `/api/datasets` and `/api/datasets/{dataset_id}`.

- [ ] **Step 1: Add repository/service failing tests.**

```python
def test_dataset_catalog_lists_only_owner_records_and_deletes_object(
    uow_factory, storage, sample_dataset_records
):
    service = DatasetCatalogService(storage=storage, uow_factory=uow_factory)
    owner_id = sample_dataset_records[0].user_id
    records, total = service.list_for_user(owner_id, offset=0, limit=20)
    assert total == 1
    assert records[0].dataset_id == sample_dataset_records[0].dataset_id
    service.delete_for_user(owner_id, sample_dataset_records[0].dataset_id)
    assert not storage.exists(sample_dataset_records[0].source_uri)
    with uow_factory() as uow:
        assert uow.datasets.get_for_user(sample_dataset_records[0].dataset_id, owner_id) is None
```

```python
def test_post_dataset_returns_profile_and_id(api_client):
    response = api_client.post(
        "/api/datasets",
        files={"file": ("sample.csv", b"name,value\na,1\n", "text/csv")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["dataset_id"]
    assert body["profile"]["row_count"] == 1
    assert "source_uri" not in body
```

- [ ] **Step 2: Run the dataset tests and verify red.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_datasets.py -q
```

Expected: FAIL because dataset catalog methods and the Router are absent.

- [ ] **Step 3: Implement owner-scoped dataset catalog operations.**

Extend `DatasetRepository` with `count_for_user`, `list_for_user(user_id, *, offset=0, limit=None)`, and `delete_for_user`. Keep the existing unbounded `list_for_user(user_id)` call compatible through the optional arguments. Extend `UnitOfWorkDatasetStore` with the same owner-scoped operations. The catalog service must fetch the record before deleting, call `storage.delete(record.source_uri)`, then delete metadata in a transaction; an unknown or foreign ID raises a stable dataset access error. Preserve the existing upload and resolver contracts.

Add DTOs:

```python
class DatasetResponse(APIModel):
    dataset_id: UUID
    name: StrictStr
    content_type: StrictStr
    size_bytes: StrictInt = Field(ge=0)
    checksum: StrictStr | None
    created_at: datetime
    profile: DatasetProfile


class DatasetListResponse(PageResponse[DatasetResponse]):
    pass
```

- [ ] **Step 4: Implement the dataset Router.**

Use `UploadFile` and `file.file` with `DatasetUploadService.upload`. Require the principal dependency for every operation. Map the catalog record profile from `metadata_json["profile"]`; never include `source_uri`. Return `201` for upload, `200` for list/detail, and `204` for delete.

- [ ] **Step 5: Run focused dataset and existing dataset regression tests.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_datasets.py tests/datasets tests/integration/test_database_lifecycle.py -q
```

Expected: all selected tests pass; oversized and invalid uploads use the unified `413`/`422` error body with a request ID.

- [ ] **Step 6: Commit the dataset task.**

```powershell
git add src/data_analysis_agent/datasets src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/api tests/api/test_datasets.py
git commit -m "feat: expose dataset API"
```
