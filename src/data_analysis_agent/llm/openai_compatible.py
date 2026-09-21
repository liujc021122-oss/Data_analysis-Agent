from collections.abc import AsyncIterator, Callable
from typing import Any

from openai import AsyncOpenAI

from ..config.llm import LLMConfig
from .models import ChatRequest, ProviderChunk, ProviderResponse, ProviderUsage


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: LLMConfig,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        factory = client_factory or AsyncOpenAI
        self._client = factory(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )
        self._closed = False

    async def chat(self, request: ChatRequest) -> ProviderResponse:
        model = request.model or self.config.model
        response = await self._client.chat.completions.create(
            **self._request_payload(request, model)
        )
        choice = response.choices[0]
        usage = self._map_usage(getattr(response, "usage", None))
        return ProviderResponse(
            text=getattr(choice.message, "content", None),
            provider=self.config.provider,
            model=getattr(response, "model", None) or model,
            finish_reason=getattr(choice, "finish_reason", None),
            request_id=getattr(response, "id", None),
            usage=usage,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ProviderChunk]:
        model = request.model or self.config.model
        response = await self._client.chat.completions.create(
            **self._request_payload(request, model, stream=True)
        )
        async for chunk in response:
            choices = getattr(chunk, "choices", ()) or ()
            choice = choices[0] if choices else None
            yield ProviderChunk(
                text=(getattr(choice.delta, "content", None) if choice else None),
                provider=self.config.provider,
                model=getattr(chunk, "model", None) or model,
                finish_reason=(getattr(choice, "finish_reason", None) if choice else None),
                request_id=getattr(chunk, "id", None),
                usage=self._map_usage(getattr(chunk, "usage", None)),
            )

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.close()

    def _request_payload(
        self, request: ChatRequest, model: str, *, stream: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
        }
        if not self._is_reasoning_model(model):
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        normalized = model.lower()
        return "reasoner" in normalized or "deepseek-r1" in normalized

    @staticmethod
    def _map_usage(usage: Any) -> ProviderUsage | None:
        if usage is None:
            return None
        return ProviderUsage(
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )


__all__ = ["OpenAICompatibleProvider"]
