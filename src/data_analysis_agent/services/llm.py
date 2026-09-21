# -*- coding: utf-8 -*-
"""
LLM调用辅助模块
"""

import asyncio
import yaml
from ..config.llm import LLMConfig
from ..llm import ChatMessage, ChatRequest, LLMClient, StructuredOutputRequest
from .errors import sanitize_exception
from .openai_client import AsyncFallbackOpenAIClient

class LLMHelper:
    """LLM调用辅助类，支持同步和异步调用"""

    def __init__(self, config: LLMConfig = None, gateway: LLMClient = None):
        self.config = config or LLMConfig()
        self.gateway = gateway or LLMClient(self.config)
        self.client = None

    def _is_reasoning_model(self) -> bool:
        """Return whether the configured model is a reasoning model."""
        model = (self.config.model or "").lower()
        return "reasoner" in model or "deepseek-r1" in model

    def _build_request_kwargs(self, max_tokens: int = None, temperature: float = None) -> dict:
        """Build model parameters while omitting unsupported reasoning options."""
        kwargs = {
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }

        if not self._is_reasoning_model():
            kwargs["temperature"] = (
                temperature if temperature is not None else self.config.temperature
            )

        return kwargs

    async def async_call(self, prompt: str, system_prompt: str = None, max_tokens: int = None, temperature: float = None) -> str:
        """异步调用LLM"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = self._build_request_kwargs(max_tokens, temperature)

        if getattr(self, "gateway", None) is not None:
            request = ChatRequest(
                messages=tuple(ChatMessage(**message) for message in messages),
                model=self.config.model,
                temperature=kwargs.get("temperature", self.config.temperature),
                max_tokens=kwargs.get("max_tokens"),
            )
            response = await self.gateway.achat(request)
            return response.text

        try:
            response = await self.client.chat_completions_create(
                messages=messages,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            print(
                "LLM调用失败: "
                + sanitize_exception(
                    e,
                    secrets=(self.config.api_key, self.config.base_url),
                    include_message=False,
                )
            )
            return ""

    def call(self, prompt: str, system_prompt: str = None, max_tokens: int = None, temperature: float = None) -> str:
        """同步调用LLM"""
        if getattr(self, "gateway", None) is not None:
            messages = []
            if system_prompt:
                messages.append(ChatMessage(role="system", content=system_prompt))
            messages.append(ChatMessage(role="user", content=prompt))
            kwargs = self._build_request_kwargs(max_tokens, temperature)
            response = self.gateway.chat(
                ChatRequest(
                    messages=tuple(messages),
                    model=self.config.model,
                    temperature=kwargs.get("temperature", self.config.temperature),
                    max_tokens=kwargs.get("max_tokens"),
                )
            )
            return response.text
        return asyncio.run(self.async_call(prompt, system_prompt, max_tokens, temperature))

    def structured_output(self, request: StructuredOutputRequest):
        if getattr(self, "gateway", None) is None:
            raise RuntimeError("LLM gateway is not configured")
        return self.gateway.structured_output(request)

    async def astructured_output(self, request: StructuredOutputRequest):
        if getattr(self, "gateway", None) is None:
            raise RuntimeError("LLM gateway is not configured")
        return await self.gateway.astructured_output(request)

    def parse_yaml_response(self, response: str) -> dict:
        """解析YAML格式的响应"""
        try:
            # 提取```yaml和```之间的内容
            if '```yaml' in response:
                start = response.find('```yaml') + 7
                end = response.find('```', start)
                yaml_content = response[start:end].strip()
            elif '```' in response:
                start = response.find('```') + 3
                end = response.find('```', start)
                yaml_content = response[start:end].strip()
            else:
                yaml_content = response.strip()

            return yaml.safe_load(yaml_content)
        except Exception as e:
            print(f"YAML解析失败: {sanitize_exception(e, include_message=False)}")
            return {}

    async def close(self):
        """关闭客户端"""
        if getattr(self, "gateway", None) is not None:
            await self.gateway.aclose()
            return
        await self.client.close()
