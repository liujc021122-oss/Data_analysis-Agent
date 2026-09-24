import json
from datetime import datetime
from math import inf, nan
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import (
    EvidenceClaimKind,
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)
from data_analysis_agent.domain.models import (
    ChartArtifact,
    EvidenceClaim,
    EvidenceValidation,
    MetricArtifact,
)


def test_metric_keeps_legacy_defaults_and_serializes_provenance():
    task_id = uuid4()
    dataset_id = uuid4()
    execution_id = uuid4()
    metric = MetricArtifact(
        task_id=task_id,
        name="revenue",
        value=125.0,
        unit="CNY",
        formula="sum(revenue)",
        source_columns=("revenue",),
        source_dataset_ids=(dataset_id,),
        execution_id=execution_id,
        code_hash="a" * 64,
        verification_status=EvidenceVerificationStatus.UNVERIFIED,
    )

    encoded = json.loads(metric.model_dump_json())

    assert encoded["task_id"] == str(task_id)
    assert encoded["source_dataset_ids"] == [str(dataset_id)]
    assert encoded["execution_id"] == str(execution_id)
    assert encoded["verification_status"] == "UNVERIFIED"
    assert MetricArtifact(name="legacy", value=1.0).task_id is None


def test_evidence_models_reject_unknown_fields_invalid_status_and_nonfinite_values():
    with pytest.raises(ValidationError, match="verification_status"):
        MetricArtifact(name="metric", value=1.0, verification_status="BROKEN")
    with pytest.raises(ValidationError, match="value"):
        MetricArtifact(name="metric", value=nan)
    with pytest.raises(ValidationError, match="recomputed_value"):
        MetricArtifact(name="metric", value=1.0, recomputed_value=inf)
    with pytest.raises(ValidationError, match="unexpected"):
        EvidenceClaim(
            task_id=uuid4(),
            text="fact",
            kind=EvidenceClaimKind.FACT,
            unexpected=True,
        )


def test_claim_and_validation_are_strict_and_json_serializable():
    task_id = uuid4()
    claim = EvidenceClaim(
        task_id=task_id,
        text="Revenue is supported by the verified metric.",
        kind=EvidenceClaimKind.FACT,
        status=EvidenceClaimStatus.SUPPORTED,
    )
    validation = EvidenceValidation(
        task_id=task_id,
        valid=True,
        claims=(claim,),
        unsupported_numeric_claims=(),
        missing_chart_ids=(),
        error_codes=(),
    )

    assert json.loads(validation.model_dump_json())["claims"][0]["status"] == "SUPPORTED"


def test_evidence_timestamps_require_timezone_information():
    with pytest.raises(ValidationError, match="computed_at"):
        MetricArtifact(
            name="metric",
            value=1.0,
            computed_at=datetime(2026, 9, 24, 10, 0),
        )
    with pytest.raises(ValidationError, match="checked_at"):
        ChartArtifact(
            filename="chart.png",
            file_path="chart.png",
            checked_at=datetime(2026, 9, 24, 10, 0),
        )
