import json
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
