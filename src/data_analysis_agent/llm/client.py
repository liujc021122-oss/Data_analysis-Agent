import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai import AuthenticationError, BadRequestError, RateLimitError

from ..config.llm import LLMConfig
from .errors import (
    LLMAuthenticationError,
    LLMClosedError,
    LLMConfigurationError,
    LLMEmptyResponseError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
)
from .models import ChatRequest, LLMCallMetrics, LLMResponse, ProviderResponse, ProviderUsage
from .openai_compatible import OpenAICompatibleProvider
from .provider import LLMProvider


class CallRecorder(Protocol):
    def record(self, metrics: LLMCallMetrics) -> None:
        ...


Sleep = Callable[[float], Awaitable[None] | None]


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        provider: LLMProvider | None = None,
        recorder: CallRecorder | None = None,
        sleep: Sleep | None = None,
        clock: Callable[[], float] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or OpenAICompatibleProvider(config)
        self.recorder = recorder
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        self._jitter = jitter or (lambda: 0.0)
        self._closed = False

    async def achat(self, request: ChatRequest) -> LLMResponse:
        self._ensure_ready()
        model = request.model.strip() if request.model and request.model.strip() else self.config.model
        effective_request = request.model_copy(update={"model": model})
        call_id = uuid4()
        started = self._clock()
        started_at = datetime.now(timezone.utc)
        attempts = 0
        last_response: ProviderResponse | None = None
        last_error: BaseException | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            attempts = attempt
            try:
                response = await asyncio.wait_for(
                    self.provider.chat(effective_request),
                    timeout=self.config.timeout_seconds,
                )
                last_response = response
                if not response.text or not response.text.strip():
                    metrics = self._metrics(
                        call_id, started, started_at, attempts, response.provider,
                        response.model or model, response.usage, response.request_id,
                        effective_request, response.text,
                    )
                    self._record(metrics)
                    raise LLMEmptyResponseError(
                        "provider returned an empty response",
                        provider=response.provider,
                        model=response.model or model,
                        attempts=attempts,
                    )
                metrics = self._metrics(
                    call_id, started, started_at, attempts, response.provider,
                    response.model or model, response.usage, response.request_id,
                    effective_request, response.text,
                )
                result = LLMResponse(
                    text=response.text,
                    provider=response.provider,
                    model=response.model or model,
                    finish_reason=response.finish_reason,
                    request_id=response.request_id,
                    metrics=metrics,
                )
                self._record(metrics)
                return result
            except BaseException as error:
                if isinstance(error, (LLMEmptyResponseError, asyncio.CancelledError)):
                    raise
                last_error = error
                mapped = self._map_error(error, model, attempts)
                if not self._is_retryable(error, mapped) or attempt >= self.config.max_attempts:
                    metrics = self._metrics(
                        call_id, started, started_at, attempts,
                        self._error_provider(error), model,
                        getattr(last_response, "usage", None),
                        getattr(last_response, "request_id", None),
                        effective_request, None,
                    )
                    self._record(metrics)
                    raise mapped from None
                delay = min(
                    self.config.backoff_max_seconds,
                    self.config.backoff_base_seconds * (2 ** (attempt - 1)),
                )
                delay = min(self.config.backoff_max_seconds, delay + max(0.0, self._jitter()))
                result = self._sleep(delay)
                if inspect.isawaitable(result):
                    await result

        raise self._map_error(last_error or RuntimeError("provider call failed"), model, attempts)

    def chat(self, request: ChatRequest) -> LLMResponse:
        return asyncio.run(self.achat(request))

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self.provider.close()

    def close(self) -> None:
        asyncio.run(self.aclose())

    def _ensure_ready(self) -> None:
        if self._closed:
            raise LLMClosedError("LLM client is closed")
        missing = []
        if not self.config.api_key or not self.config.api_key.strip():
            missing.append("OPENAI_API_KEY")
        if not self.config.base_url or not self.config.base_url.strip():
            missing.append("OPENAI_BASE_URL")
        if not self.config.model or not self.config.model.strip():
            missing.append("OPENAI_MODEL")
        if missing:
            raise LLMConfigurationError("Missing required LLM configuration: " + ", ".join(missing))

    def _metrics(
        self, call_id, started, started_at, attempts, provider, model, usage,
        request_id, request, text,
    ):
        finished = datetime.now(timezone.utc)
        final_usage = usage or self._estimate_usage(request, text)
        cost = self._cost(model, final_usage)
        return LLMCallMetrics(
            call_id=call_id,
            provider=provider or self.config.provider,
            model=model,
            attempt_count=attempts,
            started_at=started_at,
            finished_at=finished,
            duration_ms=max(0.0, (self._clock() - started) * 1000),
            usage=final_usage,
            estimated_cost_usd=cost,
            request_id=request_id,
        )

    def _record(self, metrics: LLMCallMetrics) -> None:
        if self.recorder:
            self.recorder.record(metrics)

    def _estimate_usage(self, request: ChatRequest | None, text: str | None) -> ProviderUsage:
        input_tokens = sum(len(message.content.split()) for message in request.messages) if request else 0
        output_tokens = len(text.split()) if text else 0
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated=True,
        )

    def _cost(self, model: str, usage: ProviderUsage | None) -> float | None:
        price = self.config.model_prices.get(model)
        if price is None or usage is None:
            return None
        if isinstance(price, dict):
            input_price = price.get("input", price.get("input_tokens", 0))
            output_price = price.get("output", price.get("output_tokens", 0))
        else:
            input_price = output_price = price
        return ((usage.input_tokens or 0) * input_price + (usage.output_tokens or 0) * output_price) / 1_000_000

    @staticmethod
    def _error_provider(error: BaseException) -> str:
        return getattr(error, "provider", None) or "unknown"

    @staticmethod
    def _is_retryable(original: BaseException, mapped: LLMError) -> bool:
        return (
            isinstance(original, (asyncio.TimeoutError, TimeoutError, APIConnectionError, APITimeoutError, RateLimitError))
            or (isinstance(original, APIStatusError) and 500 <= getattr(original, "status_code", 0) < 600)
            or mapped.retryable
        )

    @staticmethod
    def _map_error(error: BaseException, model: str, attempts: int) -> LLMError:
        if isinstance(error, LLMError):
            error.attempts = attempts
            if not error.model:
                error.model = model
            return error
        kwargs = {"model": model, "attempts": attempts}
        if isinstance(error, (asyncio.TimeoutError, TimeoutError, APITimeoutError)):
            return LLMTimeoutError("LLM request timed out", **kwargs)
        if isinstance(error, (APIConnectionError,)):
            return LLMNetworkError("LLM network error", **kwargs)
        if isinstance(error, RateLimitError):
            return LLMRateLimitError("LLM rate limit exceeded", **kwargs)
        if isinstance(error, AuthenticationError):
            return LLMAuthenticationError("LLM authentication failed", **kwargs)
        if isinstance(error, BadRequestError):
            return LLMRequestError("LLM request was rejected", **kwargs)
        if isinstance(error, APIStatusError):
            status = getattr(error, "status_code", None)
            cls = LLMProviderError if status and status >= 500 else LLMRequestError
            return cls("LLM provider error", status_code=status, **kwargs)
        return LLMProviderError("LLM provider error", **kwargs)


__all__ = ["CallRecorder", "LLMClient"]
