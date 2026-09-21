from uuid import UUID


class ToolError(Exception):
    code = "TOOL_ERROR"

    def __init__(self, tool_name: str, task_id: UUID, detail: str = "") -> None:
        self.tool_name = tool_name
        self.task_id = task_id
        self.detail = detail
        super().__init__(detail)


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
