from typing import Protocol, runtime_checkable

from .models import ExecutionRequest, ExecutionResult


@runtime_checkable
class CodeExecutionBackend(Protocol):
    """Synchronous port implemented by local and container executors."""

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...


__all__ = ["CodeExecutionBackend"]
