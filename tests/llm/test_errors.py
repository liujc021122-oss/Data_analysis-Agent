from data_analysis_agent.llm.errors import (
    LLMAuthenticationError,
    LLMConfigurationError,
)


def test_authentication_error_is_non_retryable_and_redacts_secrets():
    error = LLMAuthenticationError(
        "Authorization failed for private-secret-value",
        secret_values=("private-secret-value",),
    )

    assert error.code == "authentication_error"
    assert error.retryable is False
    assert "private-secret-value" not in str(error)


def test_configuration_error_has_stable_code_and_preserves_field_name():
    error = LLMConfigurationError("OPENAI_API_KEY is required")

    assert error.code == "configuration_error"
    assert error.retryable is False
    assert "OPENAI_API_KEY" in str(error)
