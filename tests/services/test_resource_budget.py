from types import SimpleNamespace

import pytest

from data_analysis_agent.config.resource import ResourceLimits
from data_analysis_agent.services.resource_budget import (
    ResourceQuotaExceededError,
    TaskBudget,
)


def test_task_budget_rejects_model_call_at_limit():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_model_calls_per_task=2),
        model_call_count=2,
        estimated_model_cost_usd=0.0,
    )

    with pytest.raises(ResourceQuotaExceededError) as raised:
        budget.check_model_call()

    assert raised.value.code == "TASK_MODEL_CALL_LIMIT"
    assert raised.value.resource == "model_calls"
    assert raised.value.retryable is False


def test_task_budget_reserves_model_call_only_after_passing():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_model_calls_per_task=2),
        model_call_count=0,
        estimated_model_cost_usd=0.0,
    )

    budget.check_model_call()
    budget.check_model_call()
    with pytest.raises(ResourceQuotaExceededError):
        budget.check_model_call()


def test_task_budget_rejects_chart_that_would_exceed_file_limit():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_chart_file_bytes=10),
        model_call_count=0,
        estimated_model_cost_usd=0.0,
    )

    with pytest.raises(ResourceQuotaExceededError) as raised:
        budget.check_chart_output(11)

    assert raised.value.code == "TASK_CHART_RESOURCE_LIMIT"
    assert raised.value.details()["limit"] == 10


def test_task_budget_rejects_chart_count_after_first_chart():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_chart_count=1, max_chart_file_bytes=10),
        model_call_count=0,
        estimated_model_cost_usd=0.0,
    )

    budget.check_chart_output(1)

    with pytest.raises(ResourceQuotaExceededError) as raised:
        budget.check_chart_output(1)

    assert raised.value.resource == "chart_count"


def test_task_budget_rejects_chart_that_would_exceed_total_limit():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_chart_total_bytes=10, max_chart_file_bytes=10),
        model_call_count=0,
        estimated_model_cost_usd=0.0,
    )

    budget.check_chart_output(6)
    with pytest.raises(ResourceQuotaExceededError) as raised:
        budget.check_chart_output(5)

    assert raised.value.resource == "chart_total_bytes"


def test_task_budget_rejects_model_usage_over_cost_limit():
    budget = TaskBudget.from_usage(
        ResourceLimits(max_model_cost_usd_per_task=1.0),
        model_call_count=0,
        estimated_model_cost_usd=0.5,
    )

    with pytest.raises(ResourceQuotaExceededError) as raised:
        budget.record_model_usage(SimpleNamespace(estimated_cost_usd=0.6))

    assert raised.value.code == "TASK_MODEL_COST_LIMIT"
    assert raised.value.resource == "model_cost_usd"

