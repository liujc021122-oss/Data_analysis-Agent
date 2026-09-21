from typing import Iterable, Optional


class LLMError(Exception):
    code = "llm_error"
    default_retryable = False

    def __init__(
        self,
        message: str = "LLM gateway error",
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        attempts: Optional[int] = None,
        retryable: Optional[bool] = None,
        secret_values: Iterable[str] = (),
    ) -> None:
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.attempts = attempts
        self.retryable = (
            self.default_retryable if retryable is None else retryable
        )
        sanitized = str(message)
        for secret in secret_values:
            if secret:
                sanitized = sanitized.replace(secret, "[REDACTED]")
        super().__init__(sanitized)


class LLMConfigurationError(LLMError):
    code = "configuration_error"


class LLMNetworkError(LLMError):
    code = "network_error"
    default_retryable = True


class LLMTimeoutError(LLMError):
    code = "timeout_error"
    default_retryable = True


class LLMRateLimitError(LLMError):
    code = "rate_limit_error"
    default_retryable = True


class LLMAuthenticationError(LLMError):
    code = "authentication_error"


class LLMRequestError(LLMError):
    code = "request_error"


class LLMProviderError(LLMError):
    code = "provider_error"


class LLMEmptyResponseError(LLMError):
    code = "empty_response_error"


class LLMStructuredOutputError(LLMError):
    code = "structured_output_error"


class LLMClosedError(LLMError):
    code = "closed_error"
