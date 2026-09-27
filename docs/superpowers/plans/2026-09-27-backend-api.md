# M13 Backend API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI backend that exposes authenticated, owner-scoped dataset, analysis-task, event, and artifact operations without executing analysis code inside the API process.

**Architecture:** Build the API through `create_app(container=None)` and an explicit `APIApplication` dependency container. Routers depend on domain services for upload, persistence, task submission, cancellation, retry, and authorized artifact access. Development/test use a UUID `X-User-ID` principal and in-memory broker/local storage; production requires an injected JWT/OIDC principal provider and uses configured persistence, storage, and Celery broker.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, existing Storage/Worker services, pytest, SQLite fixtures, fake broker.

## Global Constraints

- API routes must never call `DataAnalysisAgent`, `LLMClient`, IPython, `CodeExecutor`, or container execution directly.
- API task messages contain only `task_id`; request text, file paths, credentials, and storage URIs never enter broker payloads or public responses.
- Every resource query and mutation is scoped to the authenticated `Principal.user_id`.
- Cross-user and unknown resources return the same `404` contract.
- Production app creation must fail without an explicit non-header authentication provider.
- Tests must not require a real model API, Redis, Celery worker, MySQL server, or object-storage service.
- `X-Request-ID` is returned on every response and is included in every error body.
- Public local-file fields such as `source_uri`, `file_path`, and `storage_uri` are excluded from API responses.
- All new production behavior follows red-green-refactor: add a focused failing test, run it, implement the smallest change, run focused tests, then run the relevant regression set.

## File Map

- Create: `src/data_analysis_agent/api/app.py` for the FastAPI factory, middleware, exception handlers, and OpenAPI metadata.
- Create: `src/data_analysis_agent/api/application.py` for the injectable `APIApplication` dependency container and default development/test assembly.
- Create: `src/data_analysis_agent/api/auth.py` for `Principal`, `PrincipalProvider`, and the development/test header provider.
- Create: `src/data_analysis_agent/api/errors.py` for stable API exceptions and domain-error mapping.
- Create: `src/data_analysis_agent/api/pagination.py` for bounded pagination inputs and typed page responses.
- Create: `src/data_analysis_agent/api/routers/__init__.py`, `src/data_analysis_agent/api/routers/datasets.py`, `src/data_analysis_agent/api/routers/tasks.py`, and `src/data_analysis_agent/api/routers/artifacts.py` for the three Router modules; Task 2 registers empty routers and later tasks fill them.
- Modify: `src/data_analysis_agent/api/schemas.py` for dataset/task/event/artifact/page/error DTOs.
- Modify: `src/data_analysis_agent/api/__init__.py` to export the public API factory and DTOs.
- Modify: `src/data_analysis_agent/datasets/service.py` and `src/data_analysis_agent/datasets/__init__.py` for owner-scoped catalog and deletion operations.
- Modify: `src/data_analysis_agent/persistence/repositories.py` for owner-scoped count/page/delete/retry queries.
- Modify: `src/data_analysis_agent/services/persistence.py` for task/event/artifact query methods and transactional retry state changes.
- Modify: `src/data_analysis_agent/worker/service.py` for owner-scoped cancellation/retry orchestration.
- Modify: `src/data_analysis_agent/domain/state.py` and `src/data_analysis_agent/persistence/orm_models.py` for `FAILED -> QUEUED`.
- Create: `alembic/versions/20260927_0005_api_manual_retry.py` for the new legal transition.
- Modify: `pyproject.toml`, `requirements.txt`, and `requirements-dev.txt` for FastAPI multipart/runtime and API test dependencies.
- Create: `tests/api/conftest.py`, `tests/api/test_app.py`, `tests/api/test_datasets.py`, `tests/api/test_tasks.py`, and `tests/api/test_artifacts.py`.
- Modify: `README.md` with API startup, authentication, and endpoint examples.

## Task 1: API DTOs, pagination, authentication, and dependency setup

**Files:**
- Test: `tests/api/test_api_foundation.py`
- Modify: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`
- Create: `src/data_analysis_agent/api/auth.py`, `src/data_analysis_agent/api/errors.py`, `src/data_analysis_agent/api/pagination.py`
- Modify: `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/__init__.py`

**Interfaces:**
- Produces `Principal(user_id: UUID)`, `PrincipalProvider.current_principal(request)`, `HeaderPrincipalProvider`, `PaginationParams`, and `Page[T]`.
- Produces `DatasetResponse`, `DatasetListResponse`, `TaskListResponse`, `TaskEventListResponse`, `ArtifactDownloadResponse`, and an error DTO with `request_id`.

- [ ] **Step 1: Add the failing foundation tests.**

```python
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from data_analysis_agent.api.auth import HeaderPrincipalProvider
from data_analysis_agent.api.pagination import PageResponse, PaginationParams
from data_analysis_agent.api.schemas import ErrorResponse


def _request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
    })


def test_header_principal_requires_a_uuid_user_id():
    principal = HeaderPrincipalProvider().current_principal(
        _request({"X-User-ID": str(uuid4())})
    )
    assert isinstance(principal.user_id, UUID)

    with pytest.raises(Exception, match="authentication"):
        HeaderPrincipalProvider().current_principal(_request({}))


def test_pagination_is_bounded_and_page_response_serializes():
    assert PaginationParams(page=2, page_size=10).offset == 10
    with pytest.raises(ValidationError):
        PaginationParams(page=0)
    with pytest.raises(ValidationError):
        PaginationParams(page_size=101)

    response = PageResponse(items=(), page=1, page_size=20, total=0, has_next=False)
    assert response.model_dump(mode="json")["has_next"] is False


def test_error_response_requires_request_id():
    payload = ErrorResponse(
        code="AUTHENTICATION_REQUIRED",
        message="authentication is required",
        request_id="req-1",
    ).model_dump(mode="json")
    assert payload["request_id"] == "req-1"
```

- [ ] **Step 2: Run the foundation tests and verify the expected red state.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_api_foundation.py -q
```

Expected: FAIL because the FastAPI/API foundation modules and DTOs do not yet exist.

- [ ] **Step 3: Add API dependencies and implement the foundation contracts.**

Add these dependency entries:

```toml
"fastapi>=0.115,<1.0",
"python-multipart>=0.0.9,<1.0",
```

Add `httpx>=0.27,<1.0` to the `dev` extra and `uvicorn>=0.30,<1.0` to a new `api` extra. Keep `requirements.txt` synchronized with production dependencies and `requirements-dev.txt` as `-e .[dev,api]`.

Implement the following stable interfaces:

```python
# api/auth.py
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from fastapi import Request


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: UUID


class PrincipalProvider(Protocol):
    def current_principal(self, request: Request) -> Principal:
        ...


class HeaderPrincipalProvider:
    def current_principal(self, request: Request) -> Principal:
        raw = request.headers.get("X-User-ID", "").strip()
        try:
            return Principal(user_id=UUID(raw))
        except (ValueError, AttributeError) as exc:
            raise AuthenticationError("authentication is required") from exc
```

`api/pagination.py` must reject `page < 1` and `page_size` outside `1..100`; `offset` is `(page - 1) * page_size`. `PageResponse[T]` contains `items`, `page`, `page_size`, `total`, and `has_next`.

Extend `ErrorResponse` with required `request_id`, add response models that exclude storage paths, and export all public types from `api/__init__.py`.

- [ ] **Step 4: Run focused tests and the existing schema tests.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pip install -e '.[dev,api]'
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_api_foundation.py tests/api/test_schemas.py -q
```

Expected: all foundation and existing API schema tests pass.

- [ ] **Step 5: Commit the foundation task.**

```powershell
git add pyproject.toml requirements.txt requirements-dev.txt src/data_analysis_agent/api tests/api/test_api_foundation.py
git commit -m "feat: add API foundation contracts"
```

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

## Task 4: Task queries, events, cancellation, and manual retry

**Files:**
- Test: `tests/api/test_tasks.py`
- Modify: `src/data_analysis_agent/domain/state.py`, `src/data_analysis_agent/persistence/orm_models.py`, `src/data_analysis_agent/services/persistence.py`, `src/data_analysis_agent/worker/service.py`
- Create: `alembic/versions/20260927_0005_api_manual_retry.py`
- Modify: `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/application.py`
- Modify: `tests/api/conftest.py`, `src/data_analysis_agent/api/routers/tasks.py`

**Interfaces:**
- `TaskPersistenceService.get_task_for_user(task_id, user_id) -> AnalysisTask | None`.
- `TaskPersistenceService.list_tasks_for_user(user_id, status, offset, limit) -> tuple[list[AnalysisTask], int]`.
- `TaskPersistenceService.list_events_for_user(task_id, user_id, offset, limit) -> tuple[list[TaskEvent], int]`.
- `TaskPersistenceService.retry_failed_task(task_id, user_id) -> AnalysisTask`.
- `TaskSubmissionService.cancel_for_user(task_id, user_id)` and `retry_for_user(task_id, user_id)`.

- [ ] **Step 1: Add failing state, persistence, and HTTP tests.**

```python
def test_failed_task_can_transition_to_queued():
    assert can_transition(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_task_api_creates_once_and_lists_owner_status(api_client, broker):
    payload = {"query": "分析销售", "idempotency_key": "api-key"}
    first = api_client.post("/api/analysis-tasks", json=payload)
    second = api_client.post("/api/analysis-tasks", json=payload)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert second.json()["created"] is False
    assert len(broker.messages) == 1


def test_cross_user_task_is_not_revealed(api_client, other_user_client, task_id):
    response = other_user_client.get(f"/api/analysis-tasks/{task_id}")
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_failed_task_retry_enqueues_once(api_client, broker, failed_task):
    response = api_client.post(f"/api/analysis-tasks/{failed_task}/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert broker.messages[-1].task_id == failed_task
```

- [ ] **Step 2: Run the task tests and verify red.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_tasks.py tests/domain/test_state.py -q
```

Expected: FAIL because the transition, owner-scoped query methods, retry transaction, and task Router are absent.

- [ ] **Step 3: Add the legal retry transition and migration.**

Add `TaskStatus.QUEUED` to `LEGAL_STATUS_TRANSITIONS[TaskStatus.FAILED]` and to the ORM check constraint. Create migration `20260927_0005_api_manual_retry.py` that drops and recreates `ck_task_events_legal_status_transition`, preserving all M12 transitions and adding:

```sql
(from_status = 'FAILED' AND to_status = 'QUEUED')
```

The migration must support SQLite batch mode and MySQL upgrade/downgrade. Add an Alembic test that upgrades a fresh SQLite database to head and verifies the check constraint permits `FAILED -> QUEUED`.

- [ ] **Step 4: Implement owner-scoped task queries and retry transaction.**

Repository queries must join/filter `AnalysisTaskORM.user_id`, order by `created_at, task_id`, and return `(items, total)` after applying offset/limit. Event queries must first verify task ownership. `retry_failed_task` must lock the task, reject foreign/unknown IDs, allow only `FAILED`, clear `error_code/error_message`, transition to `QUEUED`, append one event, and commit atomically.

`TaskSubmissionService.retry_for_user(task_id, user_id) -> TaskSubmissionResult` must call the persistence method, enqueue exactly once with only the UUID, and if enqueue fails mark the task `FAILED` with `TASK_ENQUEUE_FAILED` before raising `TaskEnqueueError`. Existing M12 `cancel(task_id)` remains compatible; the API uses the owner-scoped method.

- [ ] **Step 5: Implement the task Router and response mapping.**

Add `AnalysisTaskListResponse`, `TaskRetryResponse`, and `TaskEventListResponse` using `PageResponse`. `POST /api/analysis-tasks` returns `202`; duplicate idempotent requests return the same task ID with `created=false` and no second broker message. `GET` endpoints return only owner records. Cancellation is idempotent for the owner; retry returns `409` for any non-`FAILED` status. Map persisted errors to stable nested `ErrorResponse` values without raw database text. Include Artifact summaries only through owner-scoped repository access.

- [ ] **Step 6: Run task, migration, Worker, and database regression tests.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_tasks.py tests/worker tests/domain/test_state.py tests/database/test_task_lifecycle.py tests/database/test_schema.py -q
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m alembic -x 'db_url=sqlite:///C:/Users/86136/AppData/Local/Temp/m13-api.sqlite' upgrade head
```

Expected: all selected tests pass and migration reaches `20260927_0005`.

- [ ] **Step 7: Commit the task API task.**

```powershell
git add src/data_analysis_agent/domain/state.py src/data_analysis_agent/persistence src/data_analysis_agent/services/persistence.py src/data_analysis_agent/worker/service.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/schemas.py alembic/versions/20260927_0005_api_manual_retry.py tests/api/test_tasks.py
git commit -m "feat: expose analysis task API"
```

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

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_artifacts.py -q
```

Expected: FAIL because artifact routes and API download DTO are absent.

- [ ] **Step 3: Implement owner-scoped artifact lookup and download.**

Use a per-request repository adapter backed by `UnitOfWork.artifacts.get_for_user`. Before creating a URL, call the existing `FileAccessService.create_download_url(artifact_id, user_id, expires_in=settings.storage_url_expiry)`. Map `FileAccessDeniedError` and missing artifact records to the same `404` response. Set `file_path=None` in the public DTO even when the persistence record contains a URI.

- [ ] **Step 4: Run artifact/storage regression tests.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_artifacts.py tests/storage tests/database/test_artifact_repositories.py tests/integration/test_storage_lifecycle.py -q
```

Expected: all selected tests pass, including expired/invalid local download URL behavior.

- [ ] **Step 5: Commit the artifact task.**

```powershell
git add src/data_analysis_agent/api src/data_analysis_agent/storage/access.py tests/api/test_artifacts.py
git commit -m "feat: expose authorized artifact downloads"
```

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

Run:

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

- [ ] **Step 5: Review the final diff and commit the integration task.**

```powershell
git status --short
git diff --stat
git diff --check
git add README.md src/data_analysis_agent/api src/data_analysis_agent/__init__.py tests/api
git commit -m "feat: complete M13 backend API"
```

## Self-Review Checklist

- [ ] Every requested route appears in a Router task and OpenAPI test.
- [ ] Dataset, task, event, and artifact reads are owner-scoped at repository/service level, not only in route code.
- [ ] Manual retry has a database transition, migration, duplicate-message protection, and enqueue-failure handling.
- [ ] API responses cannot expose storage URIs or local paths.
- [ ] `401`, `404`, `409`, `413`, `422`, `503`, and `500` all use the same error fields and request ID.
- [ ] Production authentication cannot silently fall back to `X-User-ID`.
- [ ] API tests run without API keys, Redis, Celery, MySQL, object storage, or a real Agent.
- [ ] Existing M00-M12 tests remain green.
