from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from ..domain.enums import (
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)
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

    def register_chart(self, chart: ChartArtifact) -> ChartArtifact:
        self._require_task(chart.task_id)
        self._charts[chart.artifact_id] = chart
        return chart

    def _chart(self, chart_id: UUID) -> ChartArtifact:
        chart = self._charts.get(chart_id)
        if chart is None:
            raise EvidenceReferenceError(
                EvidenceErrorCode.EVIDENCE_REFERENCE_NOT_FOUND,
                "chart reference was not found",
            )
        return chart

    def check_chart(self, chart_id: UUID) -> ChartArtifact:
        chart = self._chart(chart_id)
        try:
            if self.output_root is None:
                raise EvidenceError(
                    EvidenceErrorCode.CHART_PATH_INVALID,
                    "chart output root is unavailable",
                )
            candidate = Path(chart.file_path)
            if not candidate.is_absolute():
                candidate = self.output_root / candidate
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(self.output_root)
            except ValueError as exc:
                raise EvidenceError(
                    EvidenceErrorCode.CHART_PATH_INVALID,
                    "chart path is outside the task output",
                ) from exc
            if not resolved.is_file():
                raise EvidenceError(
                    EvidenceErrorCode.CHART_NOT_FOUND,
                    "chart file was not found",
                )
        except EvidenceError:
            self._charts[chart_id] = chart.model_copy(
                update={
                    "verification_status": EvidenceVerificationStatus.PENDING_CONFIRMATION,
                }
            )
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            self._charts[chart_id] = chart.model_copy(
                update={
                    "verification_status": EvidenceVerificationStatus.PENDING_CONFIRMATION,
                }
            )
            raise EvidenceError(
                EvidenceErrorCode.CHART_PATH_INVALID,
                "chart path is invalid",
            ) from exc

        checked = chart.model_copy(
            update={
                "verification_status": EvidenceVerificationStatus.VERIFIED,
                "checked_at": utc_now(),
            }
        )
        self._charts[chart_id] = checked
        return checked

    def _metric(self, metric_id: UUID) -> MetricArtifact:
        metric = self._metrics.get(metric_id)
        if metric is None:
            raise EvidenceReferenceError(
                EvidenceErrorCode.EVIDENCE_REFERENCE_NOT_FOUND,
                "metric reference was not found",
            )
        return metric

    def validate_claim(self, claim: EvidenceClaim) -> EvidenceClaim:
        self._require_task(claim.task_id)
        for metric_id in claim.metric_ids:
            self._metric(metric_id)
        for chart_id in claim.chart_ids:
            self._chart(chart_id)

        supported = any(
            self._metrics[metric_id].verification_status
            is EvidenceVerificationStatus.VERIFIED
            for metric_id in claim.metric_ids
        ) or any(
            self._charts[chart_id].verification_status
            is EvidenceVerificationStatus.VERIFIED
            for chart_id in claim.chart_ids
        )
        validated = claim.model_copy(
            update={
                "status": (
                    EvidenceClaimStatus.SUPPORTED
                    if supported
                    else EvidenceClaimStatus.PENDING_CONFIRMATION
                )
            }
        )
        self._claims[claim.claim_id] = validated
        return validated

    @staticmethod
    def _report_text_without_nonclaims(markdown: str) -> str:
        text = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"https?://\S+|www\.\S+", "", text)
        return re.sub(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            "",
            text,
        )

    def validate_report(self, markdown: str, task_id: UUID) -> EvidenceValidation:
        self._require_task(task_id)
        text = self._report_text_without_nonclaims(markdown)
        numeric_pattern = re.compile(
            r"(?<![\w.])-?(?:\d+(?:\.\d+)?|\.\d+)%?"
        )
        verified_values = tuple(
            metric.value
            for metric in self._metrics.values()
            if metric.verification_status is EvidenceVerificationStatus.VERIFIED
        )
        unsupported: list[str] = []
        for match in numeric_pattern.finditer(text):
            token = match.group(0)
            try:
                value = float(token.rstrip("%"))
            except ValueError:
                continue
            if not any(
                math.isclose(
                    value,
                    verified_value,
                    rel_tol=self.relative_tolerance,
                    abs_tol=self.absolute_tolerance,
                )
                for verified_value in verified_values
            ) and token not in unsupported:
                unsupported.append(token)

        pending_charts = tuple(
            chart_id
            for chart_id, chart in self._charts.items()
            if chart.verification_status is not EvidenceVerificationStatus.VERIFIED
        )
        claims = tuple(self._claims.values())
        error_codes: list[str] = []
        if unsupported:
            error_codes.append(EvidenceErrorCode.UNSUPPORTED_NUMERIC_CLAIM.value)
        valid = not unsupported and not pending_charts and all(
            claim.status is EvidenceClaimStatus.SUPPORTED for claim in claims
        )
        validation = EvidenceValidation(
            task_id=self.task_id,
            valid=valid,
            claims=claims,
            unsupported_numeric_claims=tuple(unsupported),
            missing_chart_ids=pending_charts,
            error_codes=tuple(error_codes),
        )
        self._validation = validation
        return validation


__all__ = ["EvidenceRegistry"]
