from __future__ import annotations

import math
from typing import Any, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, field_validator, model_validator

from data_analysis_agent.datasets.models import DatasetProfile
from data_analysis_agent.domain.models import ExecutionResult

from .errors import ToolDependencyError
from .models import ToolContext, ToolDefinition, ToolRiskLevel
from .registry import ToolRegistry


class DatasetIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID


class InspectDatasetInput(DatasetIdInput):
    model_config = ConfigDict(extra="forbid", title="DatasetIdInput")


class RunSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: StrictStr = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunSqlOutput(BaseModel):
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)


class RunPythonAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr = Field(min_length=1)


class SaveChartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: StrictStr = Field(min_length=1)
    content: StrictStr = Field(min_length=1)
    mime_type: StrictStr = Field(min_length=1)
    title: StrictStr | None = None


class GenerateReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: StrictStr = Field(min_length=1)
    content: StrictStr = Field(min_length=1)
    mime_type: StrictStr = Field(min_length=1)
    format: StrictStr = Field(min_length=1)
    title: StrictStr | None = None


class ValidateMetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1)
    value: StrictFloat
    unit: StrictStr | None = None
    minimum: StrictFloat | None = None
    maximum: StrictFloat | None = None

    @field_validator("value", "minimum", "maximum")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @model_validator(mode="after")
    def _valid_bounds(self) -> "ValidateMetricInput":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be less than or equal to maximum")
        return self


class ValidateMetricOutput(BaseModel):
    valid: bool
    name: StrictStr
    value: StrictFloat
    unit: StrictStr | None = None
    reason: StrictStr | None = None


class _MappingOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


def _dependency(name: str, context: ToolContext | None) -> ToolDependencyError:
    return ToolDependencyError(name, context.task_id if context is not None else None)


def _user_id(context: ToolContext | None) -> UUID | None:
    return context.user_id if context is not None else None


def _call_metadata_store(store: Any, dataset_id: UUID, user_id: UUID | None) -> Any:
    getter = store.get_for_user
    try:
        return getter(dataset_id, owner_id=user_id)
    except TypeError:
        return getter(dataset_id, user_id)


def _object_fields(value: Any, names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        if isinstance(value, Mapping) and name in value:
            result[name] = value[name]
        elif hasattr(value, name):
            result[name] = getattr(value, name)
    return result


def _inspect_handler(store: Any, value: InspectDatasetInput, context: ToolContext | None) -> dict[str, Any]:
    if store is None:
        raise _dependency("inspect_dataset", context)
    record = _call_metadata_store(store, value.dataset_id, _user_id(context))
    return _object_fields(
        record,
        ("dataset_id", "name", "content_type", "size_bytes", "checksum", "profile", "metadata"),
    )


def _profile_handler(resolver: Any, value: DatasetIdInput, context: ToolContext | None) -> Any:
    if resolver is None:
        raise _dependency("profile_dataset", context)
    return resolver.profile_for_user(value.dataset_id, owner_id=_user_id(context))


def _normalize_sql_result(result: Any) -> RunSqlOutput:
    if isinstance(result, tuple) and len(result) == 2:
        rows, row_count = result
    elif isinstance(result, Mapping):
        rows = result.get("rows", [])
        row_count = result.get("row_count", len(rows))
    elif isinstance(result, list):
        rows, row_count = result, len(result)
    else:
        rows, row_count = [], 0
    return RunSqlOutput(rows=list(rows), row_count=row_count)


def _sql_handler(runner: Any, value: RunSqlInput, context: ToolContext | None) -> RunSqlOutput:
    if runner is None:
        raise _dependency("run_sql", context)
    return _normalize_sql_result(runner(value.query, value.parameters))


def _python_handler(executor: Any, value: RunPythonAnalysisInput, context: ToolContext | None) -> Any:
    if executor is None:
        raise _dependency("run_python_analysis", context)
    return executor.execute_code(value.code)


def _chart_handler(saver: Any, value: SaveChartInput, context: ToolContext | None) -> dict[str, Any]:
    if saver is None:
        raise _dependency("save_chart", context)
    return saver(context.task_id, value.filename, value.content, value.mime_type, value.title)


def _report_handler(generator: Any, value: GenerateReportInput, context: ToolContext | None) -> dict[str, Any]:
    if generator is None:
        raise _dependency("generate_report", context)
    return generator(context.task_id, value.filename, value.content, value.format, value.title)


def _validate_metric(value: ValidateMetricInput, context: ToolContext | None) -> ValidateMetricOutput:
    name = value.name.strip()
    if value.minimum is not None and value.value < value.minimum:
        return ValidateMetricOutput(
            valid=False,
            name=name,
            value=value.value,
            unit=value.unit,
            reason=f"value is below minimum {value.minimum}",
        )
    if value.maximum is not None and value.value > value.maximum:
        return ValidateMetricOutput(
            valid=False,
            name=name,
            value=value.value,
            unit=value.unit,
            reason=f"value is above maximum {value.maximum}",
        )
    return ValidateMetricOutput(valid=True, name=name, value=value.value, unit=value.unit)


def _definition(
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    *,
    side_effect: bool = False,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    required_permissions: frozenset[str] = frozenset(),
    max_runtime_seconds: float = 30,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        side_effect=side_effect,
        network_access=False,
        max_runtime_seconds=max_runtime_seconds,
        required_permissions=required_permissions,
        risk_level=risk_level,
    )


def build_builtin_registry(
    metadata_store: Any = None,
    dataset_resolver: Any = None,
    sql_runner: Any = None,
    code_executor: Any = None,
    chart_saver: Any = None,
    report_generator: Any = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        _definition("generate_report", "Generate a report artifact.", GenerateReportInput, _MappingOutput, side_effect=True),
        lambda value, context: _report_handler(report_generator, value, context),
    )
    registry.register(
        _definition("inspect_dataset", "Inspect dataset metadata.", InspectDatasetInput, _MappingOutput),
        lambda value, context: _inspect_handler(metadata_store, value, context),
    )
    registry.register(
        _definition("profile_dataset", "Profile a dataset.", DatasetIdInput, DatasetProfile),
        lambda value, context: _profile_handler(dataset_resolver, value, context),
    )
    registry.register(
        _definition(
            "run_python_analysis",
            "Run Python analysis code.",
            RunPythonAnalysisInput,
            ExecutionResult,
            side_effect=True,
            risk_level=ToolRiskLevel.HIGH,
            required_permissions=frozenset({"execute:python"}),
        ),
        lambda value, context: _python_handler(code_executor, value, context),
    )
    registry.register(
        _definition("run_sql", "Run a SQL query.", RunSqlInput, RunSqlOutput),
        lambda value, context: _sql_handler(sql_runner, value, context),
    )
    registry.register(
        _definition("save_chart", "Save a chart artifact.", SaveChartInput, _MappingOutput, side_effect=True),
        lambda value, context: _chart_handler(chart_saver, value, context),
    )
    registry.register(
        _definition("validate_metric", "Validate a metric value.", ValidateMetricInput, ValidateMetricOutput),
        _validate_metric,
    )
    return registry


__all__ = [
    "DatasetIdInput",
    "InspectDatasetInput",
    "RunSqlInput",
    "RunSqlOutput",
    "RunPythonAnalysisInput",
    "SaveChartInput",
    "GenerateReportInput",
    "ValidateMetricInput",
    "ValidateMetricOutput",
    "build_builtin_registry",
]
