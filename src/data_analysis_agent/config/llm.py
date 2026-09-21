from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.1
    max_tokens: int = 8192
    timeout_seconds: float = 60.0
    max_attempts: int = 3
    backoff_base_seconds: float = 0.25
    backoff_max_seconds: float = 8.0
    model_prices: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_prices", MappingProxyType(dict(self.model_prices)))

    def __repr__(self) -> str:
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        values["api_key"] = "<redacted>" if self.api_key else None
        values["model_prices"] = dict(self.model_prices)
        fields_text = ", ".join(f"{key}={value!r}" for key, value in values.items())
        return f"LLMConfig({fields_text})"

    def to_dict(self) -> Dict[str, Any]:
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        values["api_key"] = "<redacted>" if self.api_key else None
        values["model_prices"] = dict(self.model_prices)
        return values

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMConfig":
        return cls(**data)

    def validate(self) -> bool:
        missing = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.base_url or not self.base_url.strip():
            missing.append("OPENAI_BASE_URL")
        if not self.model or not self.model.strip():
            missing.append("OPENAI_MODEL")
        if missing:
            raise ValueError(
                "Missing required LLM configuration: " + ", ".join(missing)
            )
        for field_name in (
            "timeout_seconds",
            "max_attempts",
            "backoff_base_seconds",
            "backoff_max_seconds",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        return True
