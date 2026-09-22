class AgentOrchestrationError(Exception):
    code = "ORCHESTRATION_ERROR"

    def __init__(self, message: str = "Orchestration failed", *, cause_code: str | None = None):
        self.message = message
        self.cause_code = cause_code
        super().__init__(message)


class InvalidCheckpointError(AgentOrchestrationError):
    code = "INVALID_CHECKPOINT"


class StageExecutionError(AgentOrchestrationError):
    code = "STAGE_EXECUTION_ERROR"


class DisallowedToolError(AgentOrchestrationError):
    code = "DISALLOWED_TOOL"


class ToolUnavailableError(AgentOrchestrationError):
    code = "TOOL_UNAVAILABLE"


class OrchestratorBudgetError(AgentOrchestrationError):
    code = "ORCHESTRATOR_BUDGET"
