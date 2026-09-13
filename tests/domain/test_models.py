import json
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import (
    ReportFormat,
    TaskEventType,
    TaskStatus,
    ToolCallStatus,
)
from data_analysis_agent.domain.models import (
    AgentState,
    AnalysisTask,
    ChartArtifact,
    Dataset,
    ExecutionResult,
    FrozenDict,
    MetricArtifact,
    ReportArtifact,
    TaskEvent,
    ToolCall,
)


def test_all_core_models_construct_and_serialize_to_json():
    task_id = uuid4()
    tool_call_id = uuid4()
    now = datetime.now(timezone.utc)
    task = AnalysisTask(task_id=task_id, query="分析销售数据")

    models = [
        Dataset(name="sales.csv", source_uri="sales.csv"),
        task,
        TaskEvent(
            task_id=task_id,
            event_type=TaskEventType.STATUS_CHANGED,
            from_status=TaskStatus.PENDING,
            to_status=TaskStatus.QUEUED,
            occurred_at=now,
        ),
        ToolCall(
            task_id=task_id,
            tool_name="code_executor",
            status=ToolCallStatus.SUCCEEDED,
            arguments={"code": "print(1)"},
            result={"success": True},
            started_at=now,
            finished_at=now,
        ),
        ExecutionResult(
            success=True,
            output="1",
            error=None,
            variables={"value": 1},
            duration_ms=12,
        ),
        MetricArtifact(
            name="revenue",
            value=12.5,
            unit="CNY",
            source_tool_call_id=tool_call_id,
        ),
        ChartArtifact(
            filename="trend.png",
            file_path="outputs/session/trend.png",
            title="Trend",
        ),
        ReportArtifact(
            format=ReportFormat.MARKDOWN,
            file_path="outputs/session/report.md",
            title="Report",
        ),
        AgentState(task_id=task_id, status=TaskStatus.ANALYZING, current_round=2),
    ]

    for model in models:
        encoded = model.model_dump_json()
        decoded = json.loads(encoded)
        assert isinstance(decoded, dict)
        assert encoded

    assert json.loads(task.model_dump_json())["status"] == "PENDING"
    assert json.loads(models[0].model_dump_json())["created_at"].endswith("+00:00")


def test_analysis_task_defaults_to_pending_and_preserves_m00_execution_keys():
    task = AnalysisTask(query="离线分析")
    result = ExecutionResult(success=False, error="planned failure")

    assert task.status is TaskStatus.PENDING
    assert task.dataset_ids == ()
    assert task.max_rounds == 10
    assert set(result.model_dump()) >= {"success", "output", "error", "variables"}
    assert result.success is False
    assert result.output == ""
    assert result.variables == {}


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: AnalysisTask(query="x", status="BROKEN"), "status"),
        (lambda: AnalysisTask(query="x", max_rounds="10"), "max_rounds"),
        (lambda: ExecutionResult(success="true"), "success"),
        (lambda: Dataset(name=" ", source_uri="file.csv"), "name"),
    ],
)
def test_invalid_domain_values_report_their_field(factory, field):
    with pytest.raises(ValidationError) as exc_info:
        factory()

    assert field in str(exc_info.value)


def test_unknown_fields_are_rejected_instead_of_silently_saved():
    with pytest.raises(ValidationError, match="unexpected"):
        AnalysisTask(query="x", unexpected="value")


def test_domain_snapshots_are_frozen():
    task = AnalysisTask(query="x")

    with pytest.raises(ValidationError):
        task.status = TaskStatus.RUNNING


def test_nested_mappings_and_lists_are_immutable_and_keep_json_shapes():
    task_id = uuid4()
    dataset = Dataset(
        name="sales.csv",
        source_uri="sales.csv",
        metadata={"columns": ["id", {"name": "value"}]},
    )
    tool_call = ToolCall(
        task_id=task_id,
        tool_name="code_executor",
        arguments={"steps": [{"code": "print(1)"}]},
        result={"rows": [{"value": 1}]},
    )
    execution_result = ExecutionResult(
        success=True,
        variables={"items": [{"value": 1}]},
    )
    state = AgentState(task_id=task_id, context={"history": [{"round": 1}]})

    with pytest.raises(TypeError):
        dataset.metadata["columns"] = []
    with pytest.raises((AttributeError, TypeError)):
        dataset.metadata["columns"].append("name")
    with pytest.raises(TypeError):
        dataset.metadata["columns"][1]["name"] = "changed"
    with pytest.raises(TypeError):
        tool_call.arguments["steps"][0]["code"] = "print(2)"
    with pytest.raises(TypeError):
        tool_call.result["rows"][0]["value"] = 2
    with pytest.raises(TypeError):
        execution_result.variables["items"][0]["value"] = 2
    with pytest.raises(TypeError):
        state.context["history"][0]["round"] = 2

    assert json.loads(dataset.model_dump_json())["metadata"] == {
        "columns": ["id", {"name": "value"}]
    }
    assert json.loads(tool_call.model_dump_json())["arguments"] == {
        "steps": [{"code": "print(1)"}]
    }


def test_existing_frozen_dict_is_recursively_refrozen():
    dataset = Dataset(
        name="sales.csv",
        source_uri="sales.csv",
        metadata={"nested": FrozenDict({"items": []})},
    )

    with pytest.raises((AttributeError, TypeError)):
        dataset.metadata["nested"]["items"].append("mutated")

    assert json.loads(dataset.model_dump_json())["metadata"] == {
        "nested": {"items": []}
    }


@pytest.mark.parametrize(
    "model_factory",
    [
        lambda: Dataset(name="sales.csv", source_uri="sales.csv"),
        lambda: AnalysisTask(query="x"),
        lambda: TaskEvent(
            task_id=uuid4(),
            event_type=TaskEventType.STATUS_CHANGED,
            to_status=TaskStatus.PENDING,
        ),
        lambda: ToolCall(task_id=uuid4(), tool_name="code_executor"),
        lambda: ExecutionResult(success=True),
        lambda: AgentState(task_id=uuid4()),
    ],
)
def test_default_mappings_are_empty_and_immutable(model_factory):
    model = model_factory()
    mapping_fields = [
        field_name
        for field_name in ("metadata", "arguments", "result", "variables", "context")
        if hasattr(model, field_name) and getattr(model, field_name) is not None
    ]

    for field_name in mapping_fields:
        mapping = getattr(model, field_name)
        assert mapping == {}
        with pytest.raises(TypeError):
            mapping["new"] = "value"


@pytest.mark.parametrize("mutation", [
    lambda value: dict.__setitem__(value, "bypassed", True),
    lambda value: dict.update(value, bypassed=True),
    lambda value: dict.__init__(value, bypassed=True),
])
def test_frozen_mappings_block_c_level_dict_mutation(mutation):
    dataset = Dataset(name="sales.csv", source_uri="sales.csv", metadata={"safe": True})
    metadata = dataset.metadata

    assert isinstance(metadata, Mapping)
    assert not isinstance(metadata, dict)
    with pytest.raises(TypeError):
        mutation(metadata)
    assert "bypassed" not in metadata


def test_model_copy_update_revalidates_and_deep_freezes_snapshot():
    task = AnalysisTask(query="x", metadata={"history": [{"round": 1}]})

    copied = task.model_copy(update={"metadata": {"history": [{"round": 2}]}})

    with pytest.raises(TypeError):
        copied.metadata["history"][0]["round"] = 3
    assert task.metadata["history"][0]["round"] == 1
    assert copied.metadata["history"][0]["round"] == 2

    with pytest.raises(ValidationError, match="max_rounds"):
        task.model_copy(update={"max_rounds": "10"})
    with pytest.raises(ValidationError, match="unexpected"):
        task.model_copy(update={"unexpected": "value"})

    deep_copy = task.model_copy(deep=True)
    assert deep_copy.metadata["history"][0]["round"] == 1


def test_model_construct_deep_freezes_nested_mapping_and_list():
    dataset = Dataset.model_construct(
        _fields_set={"name", "source_uri"},
        name="x",
        source_uri="x",
        metadata={"x": []},
    )

    assert dataset.model_fields_set == {"name", "source_uri"}
    with pytest.raises(TypeError):
        dataset.metadata["x"] = []
    with pytest.raises((AttributeError, TypeError)):
        dataset.metadata["x"].append("mutated")
