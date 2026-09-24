from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.errors import EvidenceError, EvidenceErrorCode
from data_analysis_agent.domain.models import MetricArtifact
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
