## Task 5: Artifact metadata and authorized download API

**Files:**
- Test: `tests/api/test_artifacts.py`
- Modify: `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/application.py`
- Modify: `tests/api/conftest.py`, `src/data_analysis_agent/api/routers/artifacts.py`
- Modify: `src/data_analysis_agent/storage/access.py` only if an owner-scoped query adapter is required.

**Interfaces:**
- `GET /api/artifacts/{artifact_id}` returns an `ArtifactResponse` without `file_path` and with an optional authorized URL.
- `GET /api/artifacts/{artifact_id}/download` returns `ArtifactDownloadResponse(artifact_id, download_url, expires_in)`.

- [ ] **Step 1: Add failing artifact tests.**

```python
def test_artifact_metadata_and_download_url_are_owner_scoped(api_client, artifact):
    metadata = api_client.get(f"/api/artifacts/{artifact.artifact_id}")
    assert metadata.status_code == 200
    assert metadata.json()["artifact_id"] == str(artifact.artifact_id)
    assert metadata.json().get("file_path") is None
    assert metadata.json()["download_url"].startswith("local-download://")

    download = api_client.get(
        f"/api/artifacts/{artifact.artifact_id}/download"
    )
    assert download.status_code == 200
    assert download.json()["expires_in"] > 0


def test_foreign_artifact_returns_same_not_found_contract(other_user_client, artifact):
    response = other_user_client.get(f"/api/artifacts/{artifact.artifact_id}")
    assert response.status_code == 404
    assert response.json()["code"] == "ARTIFACT_NOT_FOUND"
```

- [ ] **Step 2: Run artifact tests and verify red.**

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_artifacts.py -q
```

Expected: FAIL because artifact routes and API download DTO are absent.

- [ ] **Step 3: Implement owner-scoped artifact lookup and download.**

Use a per-request repository adapter backed by `UnitOfWork.artifacts.get_for_user`. Before creating a URL, call the existing `FileAccessService.create_download_url(artifact_id, user_id, expires_in=settings.storage_url_expiry)`. Map `FileAccessDeniedError` and missing artifact records to the same `404` response. Set `file_path=None` in the public DTO even when the persistence record contains a URI.

- [ ] **Step 4: Run artifact/storage regression tests.**

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_artifacts.py tests/storage tests/database/test_artifact_repositories.py tests/integration/test_storage_lifecycle.py -q
```

Expected: all selected tests pass, including expired/invalid local download URL behavior.

- [ ] **Step 5: Commit the artifact task.**

```powershell
git add src/data_analysis_agent/api src/data_analysis_agent/storage/access.py tests/api/test_artifacts.py
git commit -m "feat: expose authorized artifact downloads"
```
