from dataclasses import dataclass
import math
from typing import Any

from ..config.resource import ResourceLimits


class ResourceQuotaExceededError(RuntimeError):
    def __init__(self, code: str, resource: str, limit: int | float, observed: int | float):
        self.code = code
        self.resource = resource
        self.limit = limit
        self.observed = observed
        self.retryable = False
        super().__init__(f"{resource.replace('_', ' ')} exceeded configured limit")

    def details(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "limit": self.limit,
            "observed": self.observed,
            "retryable": self.retryable,
        }


@dataclass
class TaskBudget:
    limits: ResourceLimits
    model_call_count: int
    estimated_model_cost_usd: float
    chart_count: int = 0
    chart_total_bytes: int = 0

    @classmethod
    def from_usage(
        cls,
        limits: ResourceLimits,
        model_call_count: int,
        estimated_model_cost_usd: float,
        chart_count: int = 0,
        chart_total_bytes: int = 0,
    ) -> "TaskBudget":
        limits.validate()
        if model_call_count < 0 or chart_count < 0 or chart_total_bytes < 0:
            raise ValueError("usage counts and bytes must be non-negative")
        if not math.isfinite(estimated_model_cost_usd) or estimated_model_cost_usd < 0:
            raise ValueError("estimated_model_cost_usd must be finite and non-negative")
        return cls(limits, model_call_count, estimated_model_cost_usd, chart_count, chart_total_bytes)

    def check_model_call(self) -> None:
        observed = self.model_call_count + 1
        if observed > self.limits.max_model_calls_per_task:
            raise ResourceQuotaExceededError(
                "TASK_MODEL_CALL_LIMIT",
                "model_calls",
                self.limits.max_model_calls_per_task,
                observed,
            )
        self.model_call_count = observed

    def record_model_usage(self, metrics: Any) -> None:
        cost = getattr(metrics, "estimated_cost_usd", None)
        if cost is None:
            return
        if not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("estimated model cost must be finite and non-negative")
        observed = self.estimated_model_cost_usd + cost
        limit = self.limits.max_model_cost_usd_per_task
        if limit is not None and observed > limit:
            raise ResourceQuotaExceededError(
                "TASK_MODEL_COST_LIMIT", "model_cost_usd", limit, observed
            )
        self.estimated_model_cost_usd = observed

    def check_chart_output(self, file_bytes: int) -> None:
        if isinstance(file_bytes, bool) or not isinstance(file_bytes, int) or file_bytes < 0:
            raise ValueError("file_bytes must be a non-negative integer")
        if file_bytes > self.limits.max_chart_file_bytes:
            raise ResourceQuotaExceededError(
                "TASK_CHART_RESOURCE_LIMIT",
                "chart_file_bytes",
                self.limits.max_chart_file_bytes,
                file_bytes,
            )
        next_count = self.chart_count + 1
        if next_count > self.limits.max_chart_count:
            raise ResourceQuotaExceededError(
                "TASK_CHART_RESOURCE_LIMIT",
                "chart_count",
                self.limits.max_chart_count,
                next_count,
            )
        next_total = self.chart_total_bytes + file_bytes
        if next_total > self.limits.max_chart_total_bytes:
            raise ResourceQuotaExceededError(
                "TASK_CHART_RESOURCE_LIMIT",
                "chart_total_bytes",
                self.limits.max_chart_total_bytes,
                next_total,
            )
        self.chart_count = next_count
        self.chart_total_bytes = next_total

