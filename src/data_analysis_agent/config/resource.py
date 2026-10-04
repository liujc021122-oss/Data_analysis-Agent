from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ResourceLimits:
    max_active_tasks_per_user: int = 3
    max_model_calls_per_task: int = 20
    max_model_cost_usd_per_task: float | None = None
    max_chart_count: int = 20
    max_chart_file_bytes: int = 10 * 1024 * 1024
    max_chart_total_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_active_tasks_per_user",
            "max_model_calls_per_task",
            "max_chart_count",
            "max_chart_file_bytes",
            "max_chart_total_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        cost = self.max_model_cost_usd_per_task
        if cost is not None and (not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0):
            raise ValueError("max_model_cost_usd_per_task must be finite and non-negative")

    def validate(self) -> "ResourceLimits":
        self.__post_init__()
        return self

