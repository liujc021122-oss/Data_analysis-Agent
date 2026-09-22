import data_analysis_agent
import data_analysis_agent.tools as canonical_tools


def test_root_exports_canonical_tool_api():
    for name in canonical_tools.__all__:
        assert getattr(data_analysis_agent, name) is getattr(canonical_tools, name)


def test_root_exports_builtin_factory_and_tool_errors_from_canonical_package():
    assert (
        data_analysis_agent.build_builtin_registry
        is canonical_tools.build_builtin_registry
    )
    for name in (
        "ToolError",
        "UnknownToolError",
        "ToolInputValidationError",
        "ToolOutputValidationError",
        "ToolPermissionError",
        "ToolNetworkDeniedError",
        "ToolContextError",
        "ToolTimeoutError",
        "ToolDependencyError",
        "ToolExecutionError",
        "ToolAuditError",
    ):
        assert getattr(data_analysis_agent, name) is getattr(canonical_tools, name)
