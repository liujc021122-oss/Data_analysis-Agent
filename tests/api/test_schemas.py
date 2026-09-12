import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.api.schemas import (
    AnalysisTaskCreateRequest,
    AnalysisTaskResponse,
    ArtifactResponse,
    ErrorResponse,
    ExecutionResultResponse,
    TaskEventResponse,
)
from data_analysis_agent.domain.enums import ReportFormat, TaskEventType, TaskStatus


def test_create_request_validates_and_serializes_without_a_dataset():
    request = AnalysisTaskCreateRequest(query="分析销售数据")

    assert request.dataset_ids == ()
    assert request.max_rounds == 10
    assert json.loads(request.model_dump_json())["query"] == "分析销售数据"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"query": "   "}, "query"),
        ({"query": "分析", "max_rounds": "10"}, "max_rounds"),
    ],
)
def test_create_request_rejects_invalid_field_types_and_blank_values(payload, field):
    with pytest.raises(ValidationError) as exc_info:
        AnalysisTaskCreateRequest(**payload)

    assert field in str(exc_info.value)


def test_response_uses_the_domain_task_status_enum_and_is_not_a_domain_model():
    task_id = uuid4()
    response = AnalysisTaskResponse(
        task_id=task_id,
        query="分析销售数据",
        status=TaskStatus.REPORTING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        artifacts=(
            ArtifactResponse(
                artifact_id=uuid4(),
                artifact_type="chart",
                name="trend.png",
                file_path="outputs/trend.png",
                mime_type="image/png",
            ),
        ),
    )

    assert isinstance(response.status, TaskStatus)
    assert response.status is TaskStatus.REPORTING
    assert json.loads(response.model_dump_json())["status"] == "REPORTING"
    assert response.__class__.__name__ == "AnalysisTaskResponse"


def test_event_execution_and_error_dtos_are_json_serializable():
    now = datetime.now(timezone.utc)
    event = TaskEventResponse(
        event_id=uuid4(),
        task_id=uuid4(),
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=TaskStatus.ANALYZING,
        to_status=TaskStatus.VALIDATING,
        occurred_at=now,
    )
    execution = ExecutionResultResponse(
        success=False,
        output="partial",
        error="executor failed",
        variables={},
        duration_ms=4,
    )
    error = ErrorResponse(
        code="EXECUTION_FAILED",
        message="代码执行失败",
        details={"retryable": True},
    )

    for dto in (event, execution, error):
        assert isinstance(json.loads(dto.model_dump_json()), dict)

    assert event.to_status is TaskStatus.VALIDATING
    assert execution.success is False


def test_api_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ErrorResponse(code="E", message="m", unexpected="x")
