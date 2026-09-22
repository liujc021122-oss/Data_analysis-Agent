import pytest

from data_analysis_agent.execution.errors import (
    BackendUnavailableError,
    CleanupFailureError,
    ConfigurationMissingError,
    ContainerFailureError,
    ExecutionErrorCode,
    ExecutionTimeoutError,
    FileLimitError,
    NetworkDeniedError,
    OutputLimitError,
    PathTraversalError,
    ResourceLimitError,
    sanitize_execution_text,
)


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (ConfigurationMissingError, "CONFIGURATION_MISSING"),
        (BackendUnavailableError, "BACKEND_UNAVAILABLE"),
        (ExecutionTimeoutError, "TIMEOUT"),
        (ResourceLimitError, "RESOURCE_LIMIT"),
        (OutputLimitError, "OUTPUT_LIMIT"),
        (FileLimitError, "FILE_LIMIT"),
        (PathTraversalError, "PATH_TRAVERSAL"),
        (NetworkDeniedError, "NETWORK_DENIED"),
        (ContainerFailureError, "CONTAINER_FAILURE"),
        (CleanupFailureError, "CLEANUP_FAILURE"),
    ],
)
def test_execution_errors_have_stable_codes_and_safe_messages(error_type, code):
    secret = "private-api-key"
    host_path = r"C:\Users\analyst\repo\secret.py"
    error = error_type(
        f"failure at {host_path}; OPENAI_API_KEY={secret}",
        secrets=(secret,),
    )

    assert error.code == code
    assert str(error).startswith(f"{code}:")
    assert secret not in str(error)
    assert host_path not in str(error)
    assert ExecutionErrorCode(code).value == code


def test_sanitize_execution_text_redacts_credentials_paths_and_truncates():
    text = (
        "Bearer bearer-secret; DATABASE_URL=mysql://user:password@db/app; "
        r"path=C:\Users\analyst\repo\file.py; "
        + ("z" * 500)
    )

    sanitized = sanitize_execution_text(text, secrets=("bearer-secret",), limit=80)

    assert len(sanitized) <= 80
    assert "bearer-secret" not in sanitized
    assert "password" not in sanitized
    assert r"C:\Users\analyst\repo\file.py" not in sanitized
    assert "[truncated]" in sanitized
