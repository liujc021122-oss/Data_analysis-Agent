from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.1
    max_tokens: int = 8192

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMConfig":
        return cls(**data)

    def validate(self) -> bool:
        missing = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.base_url:
            missing.append("OPENAI_BASE_URL")
        if not self.model:
            missing.append("OPENAI_MODEL")
        if missing:
            raise ValueError(
                "Missing required LLM configuration: " + ", ".join(missing)
            )
        return True
