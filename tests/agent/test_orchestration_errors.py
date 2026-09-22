import pytest

from data_analysis_agent.agent.orchestration_errors import (
    AgentOrchestrationError,
    DisallowedToolError,
    InvalidCheckpointError,
    OrchestratorBudgetError,
    StageExecutionError,
    ToolUnavailableError,
)


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (InvalidCheckpointError, "INVALID_CHECKPOINT"),
        (StageExecutionError, "STAGE_EXECUTION_ERROR"),
        (DisallowedToolError, "DISALLOWED_TOOL"),
        (ToolUnavailableError, "TOOL_UNAVAILABLE"),
        (OrchestratorBudgetError, "ORCHESTRATOR_BUDGET"),
    ],
)
def test_concrete_orchestration_errors_have_stable_codes_and_safe_defaults(error_type, code):
    error = error_type()

    assert isinstance(error, AgentOrchestrationError)
    assert error.code == code
    assert error.message == "Orchestration failed"
    assert "prompt" not in str(error).lower()
    assert "api_key" not in str(error).lower()
    assert "\\" not in str(error)


def test_orchestration_error_preserves_safe_cause_code_without_raw_details():
    error = StageExecutionError(cause_code="MODEL_ERROR")

    assert error.cause_code == "MODEL_ERROR"
    assert "MODEL_ERROR" not in str(error)
