from uuid import UUID


class ToolError(Exception):
    code = "TOOL_ERROR"
    _safe_details = {
        "TOOL_ERROR": "Tool operation failed",
        "UNKNOWN_TOOL": "The requested tool is unavailable",
        "TOOL_INPUT_INVALID": "Tool input is invalid",
        "TOOL_OUTPUT_INVALID": "Tool output is invalid",
        "TOOL_PERMISSION_DENIED": "Tool permission denied",
        "TOOL_NETWORK_DENIED": "Tool network access denied",
        "TOOL_CONTEXT_INVALID": "Tool context is invalid",
        "TOOL_TIMEOUT": "Tool execution timed out",
        "TOOL_DEPENDENCY_FAILED": "Tool dependency failed",
        "TOOL_EXECUTION_FAILED": "Tool execution failed",
    }

    def __init__(self, tool_name: str, task_id: UUID | None = None, detail: object = "") -> None:
        self.tool_name = tool_name
        self.task_id = task_id
        self.detail = self._safe_details.get(self.code, self._safe_details["TOOL_ERROR"])
        super().__init__(f"{self.code}: {self.detail}")


class UnknownToolError(ToolError):
    code = "UNKNOWN_TOOL"


class ToolInputValidationError(ToolError):
    code = "TOOL_INPUT_INVALID"


class ToolOutputValidationError(ToolError):
    code = "TOOL_OUTPUT_INVALID"


class ToolPermissionError(ToolError):
    code = "TOOL_PERMISSION_DENIED"


class ToolNetworkDeniedError(ToolError):
    code = "TOOL_NETWORK_DENIED"


class ToolContextError(ToolError):
    code = "TOOL_CONTEXT_INVALID"


class ToolTimeoutError(ToolError):
    code = "TOOL_TIMEOUT"


class ToolDependencyError(ToolError):
    code = "TOOL_DEPENDENCY_FAILED"


class ToolExecutionError(ToolError):
    code = "TOOL_EXECUTION_FAILED"
