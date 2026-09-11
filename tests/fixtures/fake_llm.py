"""Deterministic LLM test double used by M00 contract and integration tests."""

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable, List, Optional

import yaml


@dataclass
class FakeLLMCall:
    prompt: str
    system_prompt: Optional[str]
    max_tokens: Optional[int]
    temperature: Optional[float]


class FakeLLM:
    """Replay queued responses without constructing an OpenAI client."""

    def __init__(self, responses: Iterable[Any]):
        self._responses = list(responses)
        self.calls: List[FakeLLMCall] = []

    def call(
        self,
        prompt: str,
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None,
    ) -> str:
        call = FakeLLMCall(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.calls.append(call)

        if not self._responses:
            raise AssertionError("FakeLLM response queue exhausted")

        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(self, call)
        return response

    def parse_yaml_response(self, response: str) -> dict:
        """Mirror the production parser's fenced-YAML and failure behavior."""
        try:
            if "```yaml" in response:
                start = response.find("```yaml") + 7
                end = response.find("```", start)
                yaml_content = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                yaml_content = response[start:end].strip()
            else:
                yaml_content = response.strip()
            return yaml.safe_load(yaml_content)
        except Exception:
            return {}


def yaml_response(action: str, **fields: Any) -> str:
    """Build a valid model response using the same YAML wire format."""
    payload = {"action": action}
    payload.update(fields)
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def session_dir_from_prompt(prompt: str) -> str:
    """Extract the injected session directory from an Agent system prompt."""
    match = re.search(r"session_output_dir\s*=\s*'([^']+)'", prompt or "")
    if not match:
        raise AssertionError("session_output_dir was not included in the fake LLM prompt")
    return match.group(1)
