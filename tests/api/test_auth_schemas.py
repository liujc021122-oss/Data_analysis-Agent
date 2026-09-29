from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from data_analysis_agent.api.schemas import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthUserResponse,
)
from data_analysis_agent.domain.enums import UserRole
from uuid import uuid4


def test_auth_requests_accept_a_password_at_the_minimum_length():
    register = AuthRegisterRequest(
        email="owner@example.com",
        password="a" * 12,
    )
    login = AuthLoginRequest(
        email="owner@example.com",
        password="a" * 12,
    )

    assert register.password == "a" * 12
    assert login.password == "a" * 12


@pytest.mark.parametrize("password", ["a" * 11, "a" * 129, " "])
def test_auth_requests_reject_invalid_password_lengths(password):
    with pytest.raises(ValidationError):
        AuthRegisterRequest(email="owner@example.com", password=password)
    with pytest.raises(ValidationError):
        AuthLoginRequest(email="owner@example.com", password=password)


def test_auth_user_response_contains_only_public_identity_fields():
    response = AuthUserResponse(
        user_id=uuid4(),
        email="owner@example.com",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )

    payload = response.model_dump(mode="json")

    assert payload["role"] == "ADMIN"
    assert set(payload) == {"user_id", "email", "role", "is_active", "created_at"}


def test_auth_request_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        AuthRegisterRequest(
            email="owner@example.com",
            password="a" * 12,
            role="ADMIN",
        )
