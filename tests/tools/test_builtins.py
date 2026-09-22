import math
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.builtins import (
    DatasetIdInput,
    GenerateReportInput,
    InspectDatasetInput,
    InspectDatasetOutput,
    RunPythonAnalysisInput,
    RunSqlInput,
    SaveChartInput,
    ValidateMetricInput,
    ValidateMetricOutput,
    build_builtin_registry,
)
from data_analysis_agent.tools.errors import ToolDependencyError
from data_analysis_agent.tools.executor import ToolExecutor
from data_analysis_agent.tools.models import ToolCallRequest, ToolContext, ToolRiskLevel


def test_builtin_registry_has_all_names_and_dataset_input_is_id_only():
    registry = build_builtin_registry()

    assert [item.name for item in registry.list_definitions()] == [
        "generate_report",
        "inspect_dataset",
        "profile_dataset",
        "run_python_analysis",
        "run_sql",
        "save_chart",
        "validate_metric",
    ]
    properties = DatasetIdInput.model_json_schema()["properties"]
    assert "dataset_id" in properties
    assert "source_path" not in properties
    assert InspectDatasetInput.model_json_schema() == DatasetIdInput.model_json_schema()


def test_builtin_input_models_forbid_source_paths_and_unknown_fields():
    dataset_id = uuid4()
    with pytest.raises(ValidationError):
        InspectDatasetInput(dataset_id=dataset_id, source_path="private.csv")
    with pytest.raises(ValidationError):
        RunPythonAnalysisInput(code="1", source_path="private.csv")
    with pytest.raises(ValidationError):
        RunSqlInput(query="select 1", unexpected=True)
    with pytest.raises(ValidationError):
        SaveChartInput(
            filename="chart.png",
            content="encoded",
            mime_type="image/png",
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        GenerateReportInput(
            filename="report.md",
            content="# Report",
            mime_type="text/markdown",
            format="MARKDOWN",
            unexpected=True,
        )


def test_inspect_dataset_output_is_strict_and_has_no_storage_path_fields():
    dataset_id = uuid4()
    valid = {
        "dataset_id": dataset_id,
        "name": "sales.csv",
        "content_type": "text/csv",
        "size_bytes": 12,
        "checksum": "sha256:abc",
    }

    output = InspectDatasetOutput.model_validate(valid)

    assert output.dataset_id == dataset_id
    assert InspectDatasetOutput.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        InspectDatasetOutput.model_validate(
            {**valid, "source_uri": "local://datasets/ds-1/original.csv"}
        )
    with pytest.raises(ValidationError):
        InspectDatasetOutput.model_validate(
            {**valid, "file_path": "C:/private/sales.csv"}
        )


def test_validate_metric_handler_is_pure_and_normalizes_result():
    registered = build_builtin_registry().get("validate_metric")
    value = ValidateMetricInput(name=" revenue ", value=12.5, unit="CNY")

    output = registered.handler(value, None)
    result = ValidateMetricOutput.model_validate(output)

    assert result.valid is True
    assert result.name == "revenue"
    assert result.value == 12.5
    assert result.unit == "CNY"
    assert result.reason is None


def test_validate_metric_handler_reports_range_failures_without_side_effects():
    registered = build_builtin_registry().get("validate_metric")

    below = registered.handler(
        ValidateMetricInput(name="revenue", value=2.0, minimum=3.0),
        None,
    )
    above = registered.handler(
        ValidateMetricInput(name="revenue", value=8.0, maximum=7.0),
        None,
    )

    assert ValidateMetricOutput.model_validate(below).model_dump() == {
        "valid": False,
        "name": "revenue",
        "value": 2.0,
        "unit": None,
        "reason": "value is below minimum 3.0",
    }
    assert ValidateMetricOutput.model_validate(above).reason == (
        "value is above maximum 7.0"
    )
    with pytest.raises(ValidationError):
        ValidateMetricInput(name="revenue", value=math.nan)
    with pytest.raises(ValidationError):
        ValidateMetricInput(name="revenue", value=1.0, minimum=2.0, maximum=1.0)


def test_missing_dependencies_fail_only_when_the_affected_handler_is_called():
    registry = build_builtin_registry()
    context = ToolContext(task_id=uuid4(), user_id=uuid4())

    inspect = registry.get("inspect_dataset")
    with pytest.raises(ToolDependencyError):
        inspect.handler(DatasetIdInput(dataset_id=uuid4()), context)

    profile = registry.get("profile_dataset")
    with pytest.raises(ToolDependencyError):
        profile.handler(DatasetIdInput(dataset_id=uuid4()), context)

    run_sql = registry.get("run_sql")
    with pytest.raises(ToolDependencyError):
        run_sql.handler(RunSqlInput(query="select 1"), context)

    run_python = registry.get("run_python_analysis")
    with pytest.raises(ToolDependencyError):
        run_python.handler(RunPythonAnalysisInput(code="1"), context)

    save_chart = registry.get("save_chart")
    with pytest.raises(ToolDependencyError):
        save_chart.handler(
            SaveChartInput(
                filename="chart.png",
                content="encoded",
                mime_type="image/png",
            ),
            context,
        )

    report = registry.get("generate_report")
    with pytest.raises(ToolDependencyError):
        report.handler(
            GenerateReportInput(
                filename="report.md",
                content="# Report",
                mime_type="text/markdown",
                format="MARKDOWN",
            ),
            context,
        )


def test_chart_and_report_outputs_are_typed_and_strict():
    registry = build_builtin_registry(
        chart_saver=lambda task_id, filename, content, mime_type, title: {
            "artifact_id": uuid4(),
            "filename": filename,
            "file_path": f"artifacts/{filename}",
            "mime_type": mime_type,
            "size_bytes": len(content),
            "content_hash": "sha256:chart",
            "title": title,
            "metadata": {"task_id": str(task_id)},
        },
        report_generator=lambda task_id, filename, content, report_format, title: {
            "artifact_id": uuid4(),
            "filename": filename,
            "file_path": f"artifacts/{filename}",
            "format": report_format,
            "size_bytes": len(content),
            "content_hash": "sha256:report",
            "title": title,
            "metadata": {"task_id": str(task_id)},
        },
    )
    chart_definition = registry.get("save_chart").definition
    report_definition = registry.get("generate_report").definition

    assert chart_definition.output_model.model_config["extra"] == "forbid"
    assert report_definition.output_model.model_config["extra"] == "forbid"
    assert {"artifact_id", "filename", "file_path", "mime_type", "size_bytes"}.issubset(
        chart_definition.output_model.model_fields
    )
    assert {"artifact_id", "filename", "file_path", "format", "size_bytes"}.issubset(
        report_definition.output_model.model_fields
    )

    task_id = uuid4()
    context = ToolContext(task_id=task_id)
    chart = registry.get("save_chart").handler(
        SaveChartInput(filename="chart.png", content="encoded", mime_type="image/png"),
        context,
    )
    report = registry.get("generate_report").handler(
        GenerateReportInput(
            filename="report.md",
            content="# Report",
            mime_type="text/markdown",
            format="MARKDOWN",
        ),
        context,
    )

    assert chart_definition.output_model.model_validate(chart).size_bytes == len("encoded")
    assert report_definition.output_model.model_validate(report).format == "MARKDOWN"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "callback"),
    [
        (
            "save_chart",
            {"filename": "chart.png", "content": "encoded", "mime_type": "image/png"},
            lambda *_: {"artifact_id": uuid4()},
        ),
        (
            "generate_report",
            {
                "filename": "report.md",
                "content": "# Report",
                "mime_type": "text/markdown",
                "format": "MARKDOWN",
            },
            lambda *_: {"artifact_id": uuid4(), "filename": "report.md", "unexpected": True},
        ),
    ],
)
def test_executor_rejects_invalid_artifact_outputs(tool_name, arguments, callback):
    registry = build_builtin_registry(
        chart_saver=callback if tool_name == "save_chart" else None,
        report_generator=callback if tool_name == "generate_report" else None,
    )
    task_id = uuid4()
    result = ToolExecutor(registry).execute(
        ToolCallRequest(task_id=task_id, tool_name=tool_name, arguments=arguments),
        ToolContext(task_id=task_id),
    )

    assert result.status is ToolCallStatus.FAILED
    assert result.error_code == "TOOL_OUTPUT_INVALID"


def test_executor_rejects_model_provided_source_path_before_handler_call():
    seen = []
    registry = build_builtin_registry()
    registered = registry.get("inspect_dataset")
    original_handler = registered.handler

    def tracking_handler(value, context):
        seen.append((value, context))
        return original_handler(value, context)

    registry._tools["inspect_dataset"] = registered.__class__(
        definition=registered.definition,
        handler=tracking_handler,
    )
    task_id = uuid4()
    result = ToolExecutor(registry).execute(
        ToolCallRequest(
            task_id=task_id,
            tool_name="inspect_dataset",
            arguments={"dataset_id": str(uuid4()), "source_path": "private.csv"},
        ),
        ToolContext(task_id=task_id, user_id=uuid4()),
    )

    assert result.status is ToolCallStatus.FAILED
    assert result.error_code == "TOOL_INPUT_INVALID"
    assert seen == []


def test_run_python_definition_is_high_risk_and_requires_python_permission():
    definition = build_builtin_registry().get("run_python_analysis").definition

    assert definition.risk_level is ToolRiskLevel.HIGH
    assert definition.required_permissions == frozenset({"execute:python"})
    assert definition.side_effect is True
    assert definition.network_access is False
    assert definition.max_runtime_seconds > 0


def test_all_builtin_definitions_have_explicit_policy_metadata():
    definitions = build_builtin_registry().list_definitions()

    assert all(isinstance(item.side_effect, bool) for item in definitions)
    assert all(isinstance(item.network_access, bool) for item in definitions)
    assert all(isinstance(item.required_permissions, frozenset) for item in definitions)
    assert all(item.risk_level in {ToolRiskLevel.LOW, ToolRiskLevel.HIGH} for item in definitions)
    assert all(item.max_runtime_seconds > 0 for item in definitions)
