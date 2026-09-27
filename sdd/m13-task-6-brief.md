## Task 6: Integration fixtures, documentation, packaging, and final verification

**Files:**
- Test: `tests/api/conftest.py`, `tests/api/test_app.py`, `tests/api/test_datasets.py`, `tests/api/test_tasks.py`, `tests/api/test_artifacts.py`
- Modify: `README.md`, `src/data_analysis_agent/api/__init__.py`, `src/data_analysis_agent/__init__.py` if public exports are required.

**Interfaces:**
- Tests construct `create_app(APIApplication(...))` with SQLite, local storage, `InMemoryTaskBroker`, and a fixed `Principal`.
- Public import path is `from data_analysis_agent.api import create_app`.

- [ ] **Step 1: Add failing integration assertions for the complete surface.**

```python
def test_openapi_lists_all_m13_paths(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    assert "/api/datasets" in paths
    assert "/api/analysis-tasks/{task_id}/cancel" in paths
    assert "/api/artifacts/{artifact_id}/download" in paths


def test_missing_identity_uses_uniform_error_shape(anonymous_client):
    response = anonymous_client.get("/api/datasets")
    assert response.status_code == 401
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
```

- [ ] **Step 2: Run the complete API tests and fix only integration defects.**

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api -q
```

Expected: all API tests pass with no real external service calls.

- [ ] **Step 3: Document local startup and production auth boundaries.**

Add README examples:

```powershell
pip install -e ".[dev,api]"
uvicorn data_analysis_agent.api.app:create_app --factory --reload
```

Document `X-User-ID` as development/test-only, state that production must inject JWT/OIDC, show multipart upload and JSON task submission examples, and list `/docs` and `/openapi.json`.

- [ ] **Step 4: Verify packaging and all existing behavior.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pip install -e '.[dev,api,worker]'
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m compileall -q src
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest -q
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m alembic -x 'db_url=sqlite:///C:/Users/86136/AppData/Local/Temp/m13-final.sqlite' upgrade head
git diff --check
```

Expected: editable install, compileall, Alembic head upgrade, and the full test suite exit successfully; only documented environment-dependent skips remain.

- [ ] **Step 5: Review the final diff and integrate without committing unrelated pre-existing changes.**

Review `git status --short`, `git diff --stat`, and `git diff --check`. Do not reset, checkout, or revert existing M12 work. If the integration changes cannot be isolated from prior uncommitted work, leave them uncommitted and report the exact boundary.

**Self-review checklist:**
- Every requested route appears in a Router task and OpenAPI test.
- Dataset, task, event, and artifact reads are owner-scoped at repository/service level, not only in route code.
- Manual retry has a database transition, migration, duplicate-message protection, and enqueue-failure handling.
- API responses cannot expose storage URIs or local paths.
- `401`, `404`, `409`, `413`, `422`, `503`, and `500` all use the same error fields and request ID.
- Production authentication cannot silently fall back to `X-User-ID`.
- API tests run without API keys, Redis, Celery, MySQL, object storage, or a real Agent.
- Existing M00-M12 tests remain green.
