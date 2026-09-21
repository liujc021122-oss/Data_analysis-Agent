from collections.abc import Mapping
from typing import Any

from ..config.llm import LLMConfig
from ..llm import (
    ChatMessage,
    LLMStructuredOutputError,
    StructuredOutputRequest,
)
from .schemas import AgentAction


class AgentLLMPort:
    """Typed Agent boundary with an explicit legacy FakeLLM compatibility path."""

    _ACTION_FIELDS = {
        "action",
        "code",
        "figures_to_collect",
        "final_report",
    }

    def __init__(self, helper: Any, config: LLMConfig | Any) -> None:
        self.helper = helper
        self.config = config

    def request_action(self, prompt: str, system_prompt: str | None = None) -> AgentAction:
        structured_output = getattr(self.helper, "structured_output", None)
        if callable(structured_output):
            request = StructuredOutputRequest(
                messages=self._messages(prompt, system_prompt),
                model=getattr(self.config, "model", None),
                temperature=getattr(self.config, "temperature", 0.1),
                max_tokens=getattr(self.config, "max_tokens", None),
                response_model=AgentAction,
            )
            response = structured_output(request)
            value = getattr(response, "value", response)
            return AgentAction.model_validate(value)

        # Compatibility-only branch for the pre-M06 FakeLLM contract. Real
        # gateway helpers always expose structured_output and never enter it.
        raw_response = self.helper.call(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=getattr(self.config, "max_tokens", None),
        )
        parsed = self.helper.parse_yaml_response(raw_response)
        if not isinstance(parsed, Mapping):
            raise ValueError("legacy LLM response must be a mapping")
        filtered = {
            key: parsed[key] for key in self._ACTION_FIELDS if key in parsed
        }
        return AgentAction.model_validate(filtered)

    def request_report(self, prompt: str, system_prompt: str | None = None) -> str:
        action = self.request_action(prompt, system_prompt)
        if action.action != "analysis_complete" or not action.final_report:
            raise LLMStructuredOutputError(
                "LLM report response must be an analysis_complete action"
            )
        return action.final_report

    def _messages(
        self, prompt: str, system_prompt: str | None
    ) -> tuple[ChatMessage, ...]:
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        messages.append(ChatMessage(role="user", content=prompt))
        return tuple(messages)


__all__ = ["AgentLLMPort"]
