from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, TypeAlias

from pydantic import BaseModel

from .errors import UnknownToolError
from .models import ToolContext, ToolDefinition


ToolHandler: TypeAlias = Callable[
    [BaseModel, ToolContext],
    BaseModel | Mapping[str, Any] | Awaitable[Any],
]


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        if definition.name in self._tools:
            raise ValueError(f"tool '{definition.name}' is already registered")
        self._tools[definition.name] = RegisteredTool(definition, handler)

    def get(self, name: str, task_id: Any = None) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name, task_id) from None

    def list_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            registered.definition
            for registered in sorted(self._tools.values(), key=lambda item: item.definition.name)
        )

    def model_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            registered.definition.to_model_schema()
            for registered in sorted(self._tools.values(), key=lambda item: item.definition.name)
        )
