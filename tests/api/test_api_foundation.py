from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from data_analysis_agent.api.auth import AuthenticationError, HeaderPrincipalProvider
from data_analysis_agent.api.pagination import PageResponse, PaginationParams
from data_analysis_agent.api.schemas import ErrorResponse


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in headers.items()
            ],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
        }
    )


def test_header_principal_requires_a_uuid_user_id():
    principal = HeaderPrincipalProvider().current_principal(
        _request({"X-User-ID": str(uuid4())})
    )
    assert isinstance(principal.user_id, UUID)

    with pytest.raises(AuthenticationError, match="authentication"):
        HeaderPrincipalProvider().current_principal(_request({}))

    with pytest.raises(AuthenticationError, match="authentication"):
        HeaderPrincipalProvider().current_principal(_request({"X-User-ID": "not-a-uuid"}))


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
