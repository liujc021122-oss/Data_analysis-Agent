import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.api.schemas import (
    AnalysisTaskCreateRequest,
    AnalysisTaskResponse,
    ArtifactResponse,
    DatasetUploadResponse,
    ErrorResponse,
    ExecutionResultResponse,
    TaskEventResponse,
)
from data_analysis_agent.datasets.models import (
    ColumnProfile,
    DatasetProfile,
    DatasetUploadResult,
)
from data_analysis_agent.domain.enums import ReportFormat, TaskEventType, TaskStatus


def test_create_request_validates_and_serializes_without_a_dataset():
    request = AnalysisTaskCreateRequest(query="分析销售数据", idempotency_key="contract-key")

    assert request.dataset_ids == ()
    assert request.max_rounds == 10
    assert json.loads(request.model_dump_json())["query"] == "分析销售数据"


def test_create_request_requires_a_nonblank_idempotency_key():
    with pytest.raises(ValidationError, match="idempotency_key"):
        AnalysisTaskCreateRequest(query="分析")

    with pytest.raises(ValidationError, match="idempotency_key"):
        AnalysisTaskCreateRequest(query="分析", idempotency_key="  ")


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
                download_url="local-download://opaque-token",
                mime_type="image/png",
            ),
        ),
    )

    assert isinstance(response.status, TaskStatus)
    assert response.status is TaskStatus.REPORTING
    assert json.loads(response.model_dump_json())["status"] == "REPORTING"
    assert response.artifacts[0].download_url == "local-download://opaque-token"
    public_payload = response.model_dump(mode="json")
    assert "file_path" not in public_payload["artifacts"][0]
    assert "source_uri" not in public_payload["artifacts"][0]
    assert "storage_uri" not in public_payload["artifacts"][0]
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
        request_id="req-1",
    )

    for dto in (event, execution, error):
        assert isinstance(json.loads(dto.model_dump_json()), dict)

    assert event.to_status is TaskStatus.VALIDATING
    assert execution.success is False


def test_api_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ErrorResponse(code="E", message="m", unexpected="x")

    with pytest.raises(ValidationError):
        ErrorResponse(code="E", message="m")


def test_dataset_upload_response_serializes_result_without_source_uri():
    result = DatasetUploadResult(
        dataset_id=uuid4(),
        original_filename="report.csv",
        size_bytes=12,
        checksum="sha256:abc",
        profile=DatasetProfile(
            encoding="utf-8",
            delimiter=",",
            row_count=1,
            column_count=1,
            columns=(
                ColumnProfile(
                    name="value",
                    inferred_type="integer",
                    non_null_count=1,
                    missing_count=0,
                    missing_rate=0.0,
                ),
            ),
        ),
    )

    response = DatasetUploadResponse.model_validate(result)
    payload = response.model_dump()

    assert payload["dataset_id"] == result.dataset_id
    assert response.profile == result.profile
    assert set(payload) == {"dataset_id", "profile"}
    assert "source_uri" not in payload
