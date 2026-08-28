# -*- coding: utf-8 -*-
"""
LLM调用辅助模块
"""

import asyncio
import yaml
from config.llm_config import LLMConfig
from utils.fallback_openai_client import AsyncFallbackOpenAIClient

class LLMHelper:
    """LLM调用辅助类，支持同步和异步调用"""
    
    def __init__(self, config: LLMConfig = None):
        self.config = config
        self.client = AsyncFallbackOpenAIClient(
            primary_api_key=config.api_key,
            primary_base_url=config.base_url,
            primary_model_name=config.model
        )

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
            
        try:
            response = await self.client.chat_completions_create(
                messages=messages,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM调用失败: {e}")
            return ""
    
    def call(self, prompt: str, system_prompt: str = None, max_tokens: int = None, temperature: float = None) -> str:
        """同步调用LLM"""
        return asyncio.run(self.async_call(prompt, system_prompt, max_tokens, temperature))
    
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
            print(f"YAML解析失败: {e}")
            print(f"原始响应: {response}")
            return {}
    
    async def close(self):
        """关闭客户端"""
        await self.client.close()
