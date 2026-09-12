import pytest

from data_analysis_agent.domain.enums import (
    ReportFormat,
    TaskEventType,
    TaskStatus,
    ToolCallStatus,
)
from data_analysis_agent.domain.errors import (
    DomainError,
    InvalidStatusTransitionError,
    PersistenceMappingError,
)


def test_task_status_uses_the_exact_shared_values():
    assert [status.value for status in TaskStatus] == [
        "PENDING",
        "QUEUED",
        "RUNNING",
        "EXPLORING",
        "CLEANING",
        "ANALYZING",
        "VALIDATING",
        "REPORTING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    ]


def test_event_tool_and_report_enums_are_json_friendly_strings():
    assert TaskEventType.STATUS_CHANGED.value == "STATUS_CHANGED"
    assert ToolCallStatus.SUCCEEDED.value == "SUCCEEDED"
    assert ReportFormat.MARKDOWN.value == "MARKDOWN"
    assert isinstance(TaskStatus.PENDING, str)


def test_domain_errors_have_a_stable_safe_hierarchy():
    error = InvalidStatusTransitionError(TaskStatus.COMPLETED, TaskStatus.RUNNING)

    assert isinstance(error, DomainError)
    assert isinstance(error, Exception)
    assert error.current_status is TaskStatus.COMPLETED
    assert error.target_status is TaskStatus.RUNNING
    assert "COMPLETED" in str(error)
    assert "RUNNING" in str(error)
    assert isinstance(PersistenceMappingError("invalid record"), DomainError)


def test_invalid_status_values_are_rejected_by_the_enum():
    with pytest.raises(ValueError):
        TaskStatus("BROKEN")
