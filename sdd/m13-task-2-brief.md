## Task 2: FastAPI application factory, request IDs, and errors

**Files:**
- Test: `tests/api/test_app.py`
- Create: `src/data_analysis_agent/api/app.py`, `src/data_analysis_agent/api/application.py`, `src/data_analysis_agent/api/routers/__init__.py`, `src/data_analysis_agent/api/routers/datasets.py`, `src/data_analysis_agent/api/routers/tasks.py`, `src/data_analysis_agent/api/routers/artifacts.py`
- Modify: `src/data_analysis_agent/api/auth.py`, `src/data_analysis_agent/api/errors.py`, `src/data_analysis_agent/api/__init__.py`

**Interfaces:**
- Produces `APIApplication` and `create_app(container: APIApplication | None = None) -> FastAPI`.
- `request.app.state.api_application` is the sole route dependency container.
- `get_current_principal(request)` returns the authenticated `Principal`.

- [ ] **Step 1: Add failing application tests with fake dependencies.**

```python
from uuid import uuid4

from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.config.settings import ConfigurationError


def test_request_id_is_generated_for_openapi(fake_application):
    client = TestClient(create_app(fake_application))
    response = client.get("/openapi.json")
    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert request_id


def test_supplied_request_id_is_echoed(fake_application):
    client = TestClient(create_app(fake_application))
    response = client.get("/openapi.json", headers={
        "X-Request-ID": "request-123",
    })
    assert response.headers["X-Request-ID"] == "request-123"


def test_production_requires_explicit_principal_provider(production_application):
    production_application.principal_provider = None
    with pytest.raises(ConfigurationError, match="authentication provider"):
        create_app(production_application)
```

Define `fake_application` and `production_application` in `tests/api/conftest.py` using a test `Settings` object, `None` database, a local-storage stub, no-op dataset/task/artifact service doubles, and `HeaderPrincipalProvider`. The foundation tests only exercise middleware and factory state, so these doubles must not open a database or external service. The red assertion must first fail because `create_app` is absent.

- [ ] **Step 2: Run the application tests and verify red.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_app.py -q
```

Expected: FAIL because the application factory and dependency container are absent.

- [ ] **Step 3: Implement the application container and factory.**

Use a dataclass with explicit service fields:

```python
@dataclass
class APIApplication:
    settings: Settings
    database: Database | None
    storage: Storage
    dataset_upload: DatasetUploadService
    dataset_catalog: DatasetCatalogService
    task_persistence: TaskPersistenceService
    task_submission: TaskSubmissionService
    file_access: FileAccessService
    principal_provider: PrincipalProvider | None
```

`create_app` must attach this container to `app.state.api_application`, include the three currently empty routers under `/api`, and set title/version/OpenAPI metadata. Task 3-5 replace the empty route declarations while preserving the same prefixes. Default development/test assembly may use SQLite, local storage, `InMemoryTaskBroker`, `HeaderPrincipalProvider`, and `init_database`; production assembly must use configured database/storage/Celery broker and reject a missing explicit principal provider.

Add an HTTP middleware that selects a request ID from a printable ASCII header of bounded length or generates `uuid4()`, stores it on `request.state.request_id`, and always sets `X-Request-ID` on the response.

Add exception handlers for `RequestValidationError`, `AuthenticationError`, `APIError`, mapped domain errors, and an allowlisted fallback `INTERNAL_SERVER_ERROR`. The handler must construct `ErrorResponse` with the request ID and never serialize raw exception text for the fallback.

- [ ] **Step 4: Run application tests and OpenAPI smoke checks.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_app.py -q
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -c "from data_analysis_agent.api.app import create_app; app=create_app(); assert app.openapi()['paths']"
```

Expected: application tests pass and the OpenAPI document contains the `/api` paths registered so far.

- [ ] **Step 5: Commit the application task.**

```powershell
git add src/data_analysis_agent/api tests/api/test_app.py
git commit -m "feat: add FastAPI application factory"
```
