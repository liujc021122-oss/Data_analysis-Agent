from uuid import uuid4

from data_analysis_agent.tools.errors import (
    ToolError, ToolInputValidationError, ToolPermissionError,
    ToolTimeoutError, UnknownToolError,
)


def test_errors_have_stable_codes_and_request_context():
    task_id = uuid4()
    error = ToolPermissionError("run_python_analysis", task_id, "permission denied")
    assert isinstance(error, ToolError)
    assert error.code == "TOOL_PERMISSION_DENIED"
    assert error.tool_name == "run_python_analysis"
    assert error.task_id == task_id
    assert "Authorization" not in str(error)
    assert UnknownToolError("missing", task_id).code == "UNKNOWN_TOOL"
    assert ToolInputValidationError("tool", task_id, "invalid input").code == "TOOL_INPUT_INVALID"
    assert ToolTimeoutError("tool", task_id).code == "TOOL_TIMEOUT"


def test_error_detail_is_normalized_and_does_not_leak_sensitive_input():
    task_id = uuid4()
    secret = "C:\\Users\\alice\\private.csv token=super-secret traceback=boom"
    error = ToolInputValidationError("tool", task_id, secret)

    assert error.detail != secret
    assert secret not in str(error)
    assert "private.csv" not in str(error)
    assert "super-secret" not in str(error)
    assert "traceback" not in str(error)
    assert error.code in str(error)
    assert error.tool_name == "tool"
    assert error.task_id == task_id
