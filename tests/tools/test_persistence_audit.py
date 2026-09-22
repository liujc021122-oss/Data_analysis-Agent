import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytest_plugins = ("tests.database.conftest",)

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.persistence.models import UserRecord
from data_analysis_agent.tools.audit import (
    RepositoryToolCallRecorder,
    ToolAuditRecord,
)
from data_analysis_agent.tools.errors import ToolAuditError


def make_record(*, task_id, status=ToolCallStatus.SUCCEEDED, output=None):
    started_at = datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)
    return ToolAuditRecord(
        call_id=uuid4(),
        task_id=task_id,
        tool_name="inspect_dataset",
        status=status,
        started_at=started_at,
        finished_at=started_at.replace(microsecond=125000),
        duration_ms=125,
        arguments={"dataset_id": "ds-1", "api_key": "must-not-leak"},
        output={"row_count": 3, "columns": ["x", "y"]} if output is None else output,
        error_code=None if status is ToolCallStatus.SUCCEEDED else "TOOL_EXECUTION_FAILED",
        error_message=None if status is ToolCallStatus.SUCCEEDED else "Tool execution failed",
    )


def seed_task(uow_factory):
    user_id, task_id = uuid4(), uuid4()
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=datetime.now(timezone.utc)))
        uow.tasks.add(
            user_id=user_id,
            task=AnalysisTask(task_id=task_id, query="inspect dataset"),
            idempotency_key=str(task_id),
            request_hash="request-hash",
        )
    return task_id


def test_repository_recorder_persists_tool_call_and_execution(uow_factory):
    task_id = seed_task(uow_factory)
    record = make_record(task_id=task_id)

    RepositoryToolCallRecorder(uow_factory).record(record)

    with uow_factory() as uow:
        calls = uow.tool_calls.list_for_task(task_id)
        assert calls[0].tool_call_id == record.call_id
        assert calls[0].task_id == record.task_id
        assert calls[0].tool_name == "inspect_dataset"
        assert calls[0].status is ToolCallStatus.SUCCEEDED
        assert calls[0].arguments["api_key"] == "[REDACTED]"
        assert calls[0].result == {"row_count": 3, "columns": ("x", "y")}

        executions = uow.executions.list_for_tool_call(record.call_id)
        assert executions[0].success is True
        assert executions[0].duration_ms == record.duration_ms
        assert json.loads(executions[0].output) == {"row_count": 3, "columns": ["x", "y"]}


def test_repository_recorder_derives_failed_execution_status(uow_factory):
    task_id = seed_task(uow_factory)
    record = make_record(task_id=task_id, status=ToolCallStatus.FAILED)

    RepositoryToolCallRecorder(uow_factory).record(record)

    with uow_factory() as uow:
        assert uow.tool_calls.get(record.call_id).status is ToolCallStatus.FAILED
        execution = uow.executions.list_for_tool_call(record.call_id)[0]
        assert execution.success is False
        assert execution.error == "Tool execution failed"


def test_repository_recorder_serializes_non_mapping_output_as_safe_text(uow_factory):
    task_id = seed_task(uow_factory)
    record = make_record(task_id=task_id, output=["safe", 2])

    RepositoryToolCallRecorder(uow_factory).record(record)

    with uow_factory() as uow:
        call = uow.tool_calls.get(record.call_id)
        assert call.result == '["safe",2]'
        execution = uow.executions.list_for_tool_call(record.call_id)[0]
        assert json.loads(execution.output) == ["safe", 2]
        assert execution.variables == {}


def test_repository_recorder_converts_factory_failure_to_safe_error():
    task_id = uuid4()
    record = make_record(task_id=task_id)

    def failing_factory():
        raise RuntimeError("sqlite:///private/database.sqlite3: backend secret")

    with pytest.raises(ToolAuditError) as raised:
        RepositoryToolCallRecorder(failing_factory).record(record)

    assert raised.value.code == "TOOL_AUDIT_PERSISTENCE_FAILED"
    assert "sqlite:///" not in str(raised.value)
    assert "backend secret" not in str(raised.value)


def test_repository_recorder_hides_backend_failure_and_rolls_back(uow_factory):
    task_id = seed_task(uow_factory)
    record = make_record(task_id=task_id)

    def failing_factory():
        uow = uow_factory()

        def fail(*args, **kwargs):
            raise RuntimeError("sqlite:///private/database.sqlite3: backend secret")

        uow.executions.add = fail
        return uow

    with pytest.raises(ToolAuditError) as raised:
        RepositoryToolCallRecorder(failing_factory).record(record)

    assert raised.value.code == "TOOL_AUDIT_PERSISTENCE_FAILED"
    assert str(raised.value) == "TOOL_AUDIT_PERSISTENCE_FAILED: Tool audit persistence failed"
    assert "sqlite:///" not in str(raised.value)
    assert "backend secret" not in str(raised.value)
    assert "RuntimeError" not in str(raised.value)

    with uow_factory() as uow:
        assert uow.tool_calls.list_for_task(task_id) == []


def test_executor_keeps_success_when_repository_recorder_fails():
    from data_analysis_agent.tools.executor import ToolExecutor
    from data_analysis_agent.tools.models import (
        ToolCallRequest,
        ToolContext,
        ToolDefinition,
    )
    from data_analysis_agent.tools.registry import ToolRegistry

    from pydantic import BaseModel

    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        doubled: int

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="double",
            description="double",
            input_model=Input,
            output_model=Output,
            side_effect=False,
            network_access=False,
            max_runtime_seconds=1,
            required_permissions=frozenset(),
            risk_level="LOW",
        ),
        lambda value, _: {"doubled": value.value * 2},
    )
    task_id = uuid4()
    request = ToolCallRequest(task_id=task_id, tool_name="double", arguments={"value": 4})
    recorder = RepositoryToolCallRecorder(lambda: (_ for _ in ()).throw(RuntimeError("db secret")))

    result = ToolExecutor(registry, recorder=recorder).execute(
        request,
        ToolContext(task_id=task_id),
    )

    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"doubled": 8}
