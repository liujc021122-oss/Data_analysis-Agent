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
