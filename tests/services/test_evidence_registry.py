from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import (
    EvidenceClaimKind,
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)
from data_analysis_agent.domain.errors import EvidenceError, EvidenceErrorCode
from data_analysis_agent.domain.models import ChartArtifact, EvidenceClaim, MetricArtifact
from data_analysis_agent.services.evidence import EvidenceRegistry


def reproducible_metric(task_id, *, value=10.0, name="revenue"):
    return MetricArtifact(
        task_id=task_id,
        name=name,
        value=value,
        unit="CNY",
        formula="sum(revenue)",
        source_columns=("revenue",),
        source_dataset_ids=(uuid4(),),
        execution_id=uuid4(),
        code_hash="b" * 64,
    )


def test_register_metric_is_idempotent_for_same_fact():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    first = reproducible_metric(task_id)

    registered = registry.register_metric(first)
    repeated = registry.register_metric(first.model_copy(update={"artifact_id": uuid4()}))

    assert repeated.artifact_id == registered.artifact_id
    assert len(registry.snapshot(task_id)["metrics"]) == 1


def test_register_metric_rejects_conflicting_value_for_same_fact():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    original = reproducible_metric(task_id, value=10.0)
    registry.register_metric(original)
    conflict = original.model_copy(update={"artifact_id": uuid4(), "value": 11.0})

    with pytest.raises(EvidenceError) as exc_info:
        registry.register_metric(conflict)

    assert exc_info.value.code is EvidenceErrorCode.METRIC_CONFLICT


def test_verify_metric_requires_provenance_and_records_recomputed_value():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))

    verified = registry.verify_metric(metric.artifact_id, 10.0 + 1e-10)

    assert verified.verification_status is EvidenceVerificationStatus.VERIFIED
    assert verified.recomputed_value == pytest.approx(10.0 + 1e-10)


def test_verify_metric_marks_mismatch_pending_and_raises_stable_error():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))

    with pytest.raises(EvidenceError) as exc_info:
        registry.verify_metric(metric.artifact_id, 99.0)

    assert exc_info.value.code is EvidenceErrorCode.METRIC_NOT_REPRODUCIBLE
    assert (
        registry.snapshot(task_id)["metrics"][0].verification_status
        is EvidenceVerificationStatus.PENDING_CONFIRMATION
    )


def test_registry_rejects_cross_task_metric_without_exposing_paths():
    registry = EvidenceRegistry(task_id=uuid4())
    metric = reproducible_metric(uuid4())

    with pytest.raises(EvidenceError) as exc_info:
        registry.register_metric(metric)

    assert exc_info.value.code is EvidenceErrorCode.EVIDENCE_TASK_MISMATCH
    assert "E:\\" not in str(exc_info.value)


def test_chart_check_accepts_only_regular_files_inside_output_root(tmp_path):
    task_id = uuid4()
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"png")
    registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    chart = registry.register_chart(
        ChartArtifact(
            task_id=task_id,
            filename="chart.png",
            file_path=str(chart_path),
            chart_type="line",
        )
    )

    checked = registry.check_chart(chart.artifact_id)

    assert checked.verification_status is EvidenceVerificationStatus.VERIFIED


@pytest.mark.parametrize("file_path", ["missing.png", ".", "../outside.png"])
def test_chart_check_keeps_invalid_chart_pending(tmp_path, file_path):
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    chart = registry.register_chart(
        ChartArtifact(task_id=task_id, filename="chart.png", file_path=file_path)
    )

    with pytest.raises(EvidenceError) as exc_info:
        registry.check_chart(chart.artifact_id)

    assert exc_info.value.code in {
        EvidenceErrorCode.CHART_PATH_INVALID,
        EvidenceErrorCode.CHART_NOT_FOUND,
    }
    assert (
        registry.snapshot(task_id)["charts"][0].verification_status
        is EvidenceVerificationStatus.PENDING_CONFIRMATION
    )


def test_fact_requires_verified_evidence_but_interpretation_can_be_pending():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))
    fact = EvidenceClaim(
        task_id=task_id,
        text="Revenue is verified.",
        kind=EvidenceClaimKind.FACT,
        metric_ids=(metric.artifact_id,),
    )
    interpretation = EvidenceClaim(
        task_id=task_id,
        text="The trend may indicate seasonality.",
        kind=EvidenceClaimKind.INTERPRETATION,
    )

    assert registry.validate_claim(fact).status is EvidenceClaimStatus.PENDING_CONFIRMATION
    assert (
        registry.validate_claim(interpretation).status
        is EvidenceClaimStatus.PENDING_CONFIRMATION
    )
    registry.verify_metric(metric.artifact_id, 10.0)
    assert registry.validate_claim(fact).status is EvidenceClaimStatus.SUPPORTED


def test_report_numeric_validation_accepts_verified_values_and_flags_unknown_numbers():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id, value=10.0))
    registry.verify_metric(metric.artifact_id, 10.0)

    validation = registry.validate_report(
        "Revenue was 10. The identifier is 123e4567-e89b-12d3-a456-426614174000.\n"
        "```python\nprint(999)\n```\n",
        task_id,
    )
    unsupported = registry.validate_report("Revenue was 77.", task_id)

    assert validation.valid is True
    assert unsupported.valid is False
    assert "77" in unsupported.unsupported_numeric_claims
    assert EvidenceErrorCode.UNSUPPORTED_NUMERIC_CLAIM.value in unsupported.error_codes
