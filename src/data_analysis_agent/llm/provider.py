from collections.abc import AsyncIterator
from typing import Protocol

from ..config.llm import LLMConfig
from .models import ChatRequest, ProviderChunk, ProviderResponse


class LLMProvider(Protocol):
    async def chat(self, request: ChatRequest) -> ProviderResponse:
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ProviderChunk]:
        ...

    async def close(self) -> None:
        ...


__all__ = ["LLMProvider"]
