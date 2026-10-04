import pytest

from data_analysis_agent.config.resource import ResourceLimits
from data_analysis_agent.config.settings import load_settings


def test_settings_reads_resource_limits_from_environment():
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "MAX_ACTIVE_TASKS_PER_USER": "4",
            "MAX_MODEL_CALLS_PER_TASK": "7",
            "MAX_CHART_COUNT": "8",
            "MAX_CHART_FILE_BYTES": "1024",
            "MAX_CHART_TOTAL_BYTES": "4096",
        },
    )

    limits = settings.resource_limits()
    assert limits.max_active_tasks_per_user == 4
    assert limits.max_model_calls_per_task == 7
    assert limits.max_chart_total_bytes == 4096


def test_settings_resource_limits_returns_new_immutable_value_object():
    settings = load_settings(app_env="test", environ={})

    first = settings.resource_limits()
    second = settings.resource_limits()

    assert first == second
    assert first is not second
    with pytest.raises(AttributeError):
        first.max_chart_count = 99


def test_resource_limits_reject_non_finite_or_non_positive_values():
    with pytest.raises(ValueError):
        ResourceLimits(max_active_tasks_per_user=0)
    with pytest.raises(ValueError):
        ResourceLimits(max_model_cost_usd_per_task=float("nan"))

