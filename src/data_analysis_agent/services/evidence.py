from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from uuid import UUID

from ..domain.enums import EvidenceVerificationStatus
from ..domain.errors import (
    EvidenceError,
    EvidenceErrorCode,
    EvidenceReferenceError,
)
from ..domain.models import (
    ChartArtifact,
    EvidenceClaim,
    EvidenceValidation,
    MetricArtifact,
    utc_now,
)


class EvidenceRegistry:
    """Keep traceable analysis evidence for one task in memory."""

    def __init__(
        self,
        *,
        task_id: UUID,
        output_root: str | Path | None = None,
        absolute_tolerance: float = 1e-9,
        relative_tolerance: float = 1e-6,
    ) -> None:
        if not math.isfinite(absolute_tolerance) or absolute_tolerance < 0:
            raise ValueError("absolute_tolerance must be finite and non-negative")
        if not math.isfinite(relative_tolerance) or relative_tolerance < 0:
            raise ValueError("relative_tolerance must be finite and non-negative")
        self.task_id = task_id
        self.output_root = (
            Path(output_root).resolve(strict=False) if output_root is not None else None
        )
        self.absolute_tolerance = absolute_tolerance
        self.relative_tolerance = relative_tolerance
        self._metrics: dict[UUID, MetricArtifact] = {}
        self._metric_keys: dict[tuple[Any, ...], UUID] = {}
        self._charts: dict[UUID, ChartArtifact] = {}
        self._claims: dict[UUID, EvidenceClaim] = {}
        self._validation: EvidenceValidation | None = None

    def _require_task(self, evidence_task_id: UUID | None) -> None:
        if evidence_task_id != self.task_id:
            raise EvidenceError(
                EvidenceErrorCode.EVIDENCE_TASK_MISMATCH,
                "evidence belongs to another task",
            )

    @staticmethod
    def _metric_key(metric: MetricArtifact) -> tuple[Any, ...]:
        return (
            metric.name,
            metric.unit,
            metric.formula,
            metric.execution_id,
            metric.source_dataset_ids,
            metric.source_columns,
        )

    @staticmethod
    def _has_provenance(metric: MetricArtifact) -> bool:
        return bool(
            metric.task_id
            and metric.source_dataset_ids
            and metric.execution_id
            and metric.code_hash
        )

    def register_metric(self, metric: MetricArtifact) -> MetricArtifact:
        self._require_task(metric.task_id)
        key = self._metric_key(metric)
        existing_id = self._metric_keys.get(key)
        if existing_id is not None:
            existing = self._metrics[existing_id]
            if not math.isclose(
                existing.value,
                metric.value,
                rel_tol=self.relative_tolerance,
                abs_tol=self.absolute_tolerance,
            ):
                raise EvidenceError(
                    EvidenceErrorCode.METRIC_CONFLICT,
                    "metric fact conflicts with a registered value",
                )
            return existing

        if (
            metric.verification_status is EvidenceVerificationStatus.VERIFIED
            and not self._has_provenance(metric)
        ):
            raise EvidenceError(
                EvidenceErrorCode.METRIC_NOT_REPRODUCIBLE,
                "verified metric is missing provenance",
            )
        self._metrics[metric.artifact_id] = metric
        self._metric_keys[key] = metric.artifact_id
        return metric

    def verify_metric(self, metric_id: UUID, recomputed_value: float) -> MetricArtifact:
        metric = self._metrics.get(metric_id)
        if metric is None:
            raise EvidenceReferenceError(
                EvidenceErrorCode.EVIDENCE_REFERENCE_NOT_FOUND,
                "metric reference was not found",
            )
        try:
            finite_value = float(recomputed_value)
        except (TypeError, ValueError) as exc:
            finite_value = float("nan")
            del exc
        matches = math.isfinite(finite_value) and math.isclose(
            metric.value,
            finite_value,
            rel_tol=self.relative_tolerance,
            abs_tol=max(self.absolute_tolerance, metric.tolerance),
        )
        if not matches or not self._has_provenance(metric):
            pending = metric.model_copy(
                update={
                    "recomputed_value": finite_value,
                    "verification_status": EvidenceVerificationStatus.PENDING_CONFIRMATION,
                }
            )
            self._metrics[metric_id] = pending
            raise EvidenceError(
                EvidenceErrorCode.METRIC_NOT_REPRODUCIBLE,
                "metric could not be reproduced",
            )

        verified = metric.model_copy(
            update={
                "recomputed_value": finite_value,
                "computed_at": metric.computed_at or utc_now(),
                "verification_status": EvidenceVerificationStatus.VERIFIED,
            }
        )
        self._metrics[metric_id] = verified
        return verified

    def snapshot(self, task_id: UUID) -> dict[str, Any]:
        self._require_task(task_id)
        return {
            "metrics": tuple(self._metrics.values()),
            "charts": tuple(self._charts.values()),
            "claims": tuple(self._claims.values()),
            "validation": self._validation,
        }


__all__ = ["EvidenceRegistry"]
