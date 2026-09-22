# M08 Agent State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a deterministic `AgentOrchestrator` with typed stage contracts, lifecycle control, budgets, cancellation, checkpoint recovery, tool-stage policy, and idempotent reporting while preserving the existing `DataAnalysisAgent` and `quick_analysis()` contracts.

**Architecture:** Add a provider-neutral orchestration model layer and a synchronous orchestrator around the existing domain state and tool executor. A `LegacyAnalysisAdapter` maps the current LLM/action, code-execution, chart-collection, and report-generation behavior into the fixed stages. `DataAnalysisAgent._analyze_impl()` keeps dataset/session setup and delegates the execution loop to that adapter; legacy result dictionaries remain the public compatibility shape.

**Tech Stack:** Python 3.10+, Pydantic 2, existing `TaskStatus`/`AgentState`/`TaskEvent` domain models, existing `ToolRegistry`/`ToolExecutor`, pytest, offline `FakeLLM`, local filesystem fixtures.

## Global Constraints

- Do not add LangGraph, a queue, a new database table, or a database checkpoint migration.
- Do not remove YAML/action compatibility helpers or change the public signatures of `DataAnalysisAgent.analyze()` and `quick_analysis()`.
- All orchestration models must reject extra fields, validate JSON-safe payloads, and support `model_dump(mode="json")`.
- The fixed stage order is `RUNNING -> EXPLORING -> CLEANING -> ANALYZING -> VALIDATING -> REPORTING -> COMPLETED` after `PENDING -> QUEUED -> RUNNING` initialization.
- All state changes must use the existing domain transition rules; terminal states are `COMPLETED`, `FAILED`, and `CANCELLED`.
- All tests must use fake LLMs, fake handlers, in-memory tools, or local temporary files; no real model API, network service, or production database.
- No model prompt may contain an uploaded source path; datasets remain identified by dataset IDs.
- Production code must be written only after the corresponding test has failed for the intended missing behavior.
- Existing M00-M07 tests must remain green after every task that changes production behavior.

---

### Task 1: Add typed orchestration models and stable errors

**Files:**
- Create: `src/data_analysis_agent/agent/orchestration_models.py`
- Create: `src/data_analysis_agent/agent/orchestration_errors.py`
- Create: `tests/agent/__init__.py`
- Create: `tests/agent/test_orchestration_models.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`

**Interfaces:**
- Consumes: `AnalysisTask`, `AgentState`, `TaskStatus`, `ToolCallResult` and Pydantic 2 from the existing package.
- Produces: `OrchestratorLimits`, `StageInput`, `StageFailure`, `StageResult`, `AgentCheckpoint`, `OrchestrationResult`, `StageToolCaller`, `StageHandler`, and stable orchestration error classes for later tasks.

- [ ] **Step 1: Write the failing model tests**

```python
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.agent.orchestration_models import (
    AgentCheckpoint,
    OrchestratorLimits,
    StageFailure,
    StageInput,
    StageResult,
)
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask


def test_stage_result_rejects_a_failed_result_marked_completed():
    with pytest.raises(ValidationError, match="failed stage result"):
        StageResult(
            completed=True,
            failure=StageFailure(code="MODEL_ERROR", message="offline failure"),
        )


def test_checkpoint_round_trips_to_json_with_task_and_state_statuses():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id, status=TaskStatus.PENDING)
    checkpoint = AgentCheckpoint(
        task=task,
        state=state,
        step_number=0,
        context={"dataset_id": str(uuid4())},
        output={"report": "# offline"},
    )

    restored = AgentCheckpoint.model_validate_json(
        checkpoint.model_dump_json()
    )

    assert restored.task.task_id == task.task_id
    assert restored.state.status is TaskStatus.PENDING
    assert restored.output == {"report": "# offline"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_steps", 0),
        ("max_model_calls", 0),
        ("max_runtime_seconds", 0),
        ("max_stage_retries", -1),
    ],
)
def test_orchestrator_limits_reject_non_positive_values(field, value):
    with pytest.raises(ValidationError):
        OrchestratorLimits(**{field: value})


def test_stage_input_rejects_negative_step_and_model_budget():
    task = AnalysisTask(query="inspect sales")
    state = AgentState(task_id=task.task_id)

    with pytest.raises(ValidationError):
        StageInput(
            task=task,
            state=state,
            stage=TaskStatus.RUNNING,
            step_number=-1,
            attempt=0,
            remaining_model_calls=1,
        )

    with pytest.raises(ValidationError):
        StageInput(
            task=task,
            state=state,
            stage=TaskStatus.RUNNING,
            step_number=0,
            attempt=0,
            remaining_model_calls=-1,
        )


def test_extra_orchestration_fields_are_rejected():
    with pytest.raises(ValidationError, match="extra_field"):
        OrchestratorLimits(extra_field=1)
```

- [ ] **Step 2: Run the focused tests to verify the expected missing-module failure**

Run: `pytest tests/agent/test_orchestration_models.py -q`

Expected: collection fails because `data_analysis_agent.agent.orchestration_models` does not exist yet.

- [ ] **Step 3: Implement the model and error contracts**

Use the following public shape in `orchestration_models.py`:

```python
from collections.abc import Mapping
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from ..domain.enums import TaskStatus
from ..domain.models import AgentState, AnalysisTask
from ..tools.models import ToolCallResult


class OrchestrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OrchestratorLimits(OrchestrationModel):
    max_steps: StrictInt = Field(default=50, gt=0)
    max_model_calls: StrictInt = Field(default=20, gt=0)
    max_runtime_seconds: StrictFloat = Field(default=900.0, gt=0)
    max_stage_retries: StrictInt = Field(default=2, ge=0)


class StageInput(OrchestrationModel):
    task: AnalysisTask
    state: AgentState
    stage: TaskStatus
    step_number: StrictInt = Field(ge=0)
    attempt: StrictInt = Field(ge=0)
    allowed_tools: frozenset[StrictStr] = frozenset()
    remaining_model_calls: StrictInt = Field(ge=0)
    context: dict[str, JsonValue] = Field(default_factory=dict)


class StageFailure(OrchestrationModel):
    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    retryable: StrictBool = False


class StageResult(OrchestrationModel):
    completed: StrictBool = True
    context_updates: dict[str, JsonValue] = Field(default_factory=dict)
    output: JsonValue | None = None
    model_calls: StrictInt = Field(default=0, ge=0)
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def validate_failure_state(self) -> "StageResult":
        if self.failure is not None and self.completed:
            raise ValueError("failed stage result cannot be marked completed")
        return self


class AgentCheckpoint(OrchestrationModel):
    version: Literal[1] = 1
    task: AnalysisTask
    state: AgentState
    step_number: StrictInt = Field(ge=0)
    stage_attempts: dict[StrictStr, StrictInt] = Field(default_factory=dict)
    report_generated: StrictBool = False
    elapsed_runtime_seconds: StrictFloat = Field(default=0.0, ge=0)
    context: dict[str, JsonValue] = Field(default_factory=dict)
    output: dict[str, JsonValue] = Field(default_factory=dict)


class OrchestrationResult(OrchestrationModel):
    task_id: UUID
    status: TaskStatus
    state: AgentState
    output: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    checkpoint: AgentCheckpoint


class StageToolCaller(Protocol):
    def __call__(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolCallResult:
        ...


class StageHandler(Protocol):
    def __call__(
        self, stage_input: StageInput, call_tool: StageToolCaller
    ) -> StageResult:
        ...
```

Define `AgentOrchestrationError` in `orchestration_errors.py` with `code`, `message`, and optional `cause_code`, plus these concrete errors: `InvalidCheckpointError`, `StageExecutionError`, `DisallowedToolError`, `ToolUnavailableError`, and `OrchestratorBudgetError`. Each error must expose a safe `code` string and must not include prompts, API keys, local paths, or raw exception text by default. `AgentOrchestrationError` is the single generic error used by the legacy facade when an orchestration result is not successful.

Export the models, protocols, and errors from `src/data_analysis_agent/agent/__init__.py`; do not change the root package exports until Task 7.

- [ ] **Step 4: Run the focused tests to verify the model implementation**

Run: `pytest tests/agent/test_orchestration_models.py -q`

Expected: all model tests pass.

- [ ] **Step 5: Commit the typed contract**

```powershell
git add src/data_analysis_agent/agent/orchestration_models.py src/data_analysis_agent/agent/orchestration_errors.py src/data_analysis_agent/agent/__init__.py tests/agent
git commit -m "feat: add typed orchestration contracts"
```

### Task 2: Implement fixed lifecycle sequencing and state events

**Files:**
- Create: `src/data_analysis_agent/agent/orchestrator.py`
- Create: `tests/agent/test_orchestrator_lifecycle.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`

**Interfaces:**
- Consumes: Task 1 models/errors, existing `transition_task()`, `AnalysisTask`, `AgentState`, `TaskEvent`, and `TaskEventType`.
- Produces: `AgentOrchestrator(task=..., handlers=..., limits=..., tool_executor=..., tool_context_factory=..., initial_state=...)`, `run()`, `cancel()`, `checkpoint()`, `call_tool()`, fixed stage constants, and per-stage allowed-tool constants.

- [ ] **Step 1: Write the failing lifecycle tests**

```python
from collections import defaultdict

from data_analysis_agent.agent.orchestration_models import StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AnalysisTask


def _handlers(trace):
    def make(stage):
        def handler(stage_input, call_tool):
            trace.append((stage, stage_input.stage, stage_input.attempt))
            return StageResult(output={"stage": stage.value})

        return handler

    return {stage: make(stage) for stage in AgentOrchestrator.ACTIVE_STAGES}


def test_orchestrator_runs_the_declared_stage_order_and_reaches_completed():
    trace = []
    task = AnalysisTask(query="offline flow")
    orchestrator = AgentOrchestrator(task=task, handlers=_handlers(trace))

    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert [stage for stage, _, _ in trace] == [
        TaskStatus.RUNNING,
        TaskStatus.EXPLORING,
        TaskStatus.CLEANING,
        TaskStatus.ANALYZING,
        TaskStatus.VALIDATING,
        TaskStatus.REPORTING,
    ]
    assert result.state.status is TaskStatus.COMPLETED
    assert [event.to_status for event in result.state.events[:2]] == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ]


def test_missing_active_stage_handler_is_rejected_before_execution():
    task = AnalysisTask(query="offline flow")
    handlers = _handlers([])
    handlers.pop(TaskStatus.REPORTING)

    with pytest.raises(ValueError, match="REPORTING"):
        AgentOrchestrator(task=task, handlers=handlers)


def test_task_and_state_status_mismatch_in_initial_state_is_rejected():
    task = AnalysisTask(query="offline flow")
    state = AgentState(task_id=task.task_id, status=TaskStatus.RUNNING)

    with pytest.raises(ValueError, match="status"):
        AgentOrchestrator(task=task, handlers=_handlers([]), initial_state=state)
```

Add `from data_analysis_agent.domain.models import AgentState` and `import pytest` to the test file. The first run must fail because the orchestrator module and `ACTIVE_STAGES` do not exist.

- [ ] **Step 2: Run the lifecycle tests to verify RED**

Run: `pytest tests/agent/test_orchestrator_lifecycle.py -q`

Expected: collection fails with a missing `AgentOrchestrator` import.

- [ ] **Step 3: Implement the minimal lifecycle engine**

Define these constants and core invariants in `orchestrator.py`:

```python
ACTIVE_STAGES = (
    TaskStatus.RUNNING,
    TaskStatus.EXPLORING,
    TaskStatus.CLEANING,
    TaskStatus.ANALYZING,
    TaskStatus.VALIDATING,
    TaskStatus.REPORTING,
)

TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

NEXT_STAGE = {
    TaskStatus.RUNNING: TaskStatus.EXPLORING,
    TaskStatus.EXPLORING: TaskStatus.CLEANING,
    TaskStatus.CLEANING: TaskStatus.ANALYZING,
    TaskStatus.ANALYZING: TaskStatus.VALIDATING,
    TaskStatus.VALIDATING: TaskStatus.REPORTING,
    TaskStatus.REPORTING: TaskStatus.COMPLETED,
}

STAGE_ALLOWED_TOOLS = {
    TaskStatus.RUNNING: frozenset(),
    TaskStatus.EXPLORING: frozenset({"inspect_dataset", "profile_dataset"}),
    TaskStatus.CLEANING: frozenset({"run_python_analysis"}),
    TaskStatus.ANALYZING: frozenset(
        {"run_sql", "run_python_analysis", "save_chart", "validate_metric"}
    ),
    TaskStatus.VALIDATING: frozenset({"validate_metric", "inspect_dataset"}),
    TaskStatus.REPORTING: frozenset({"generate_report"}),
}
```

Expose `ACTIVE_STAGES`, `NEXT_STAGE`, `STAGE_ALLOWED_TOOLS`, and `TERMINAL_STATUSES` as class attributes on `AgentOrchestrator` as well as module constants, because tests and later adapters use `AgentOrchestrator.ACTIVE_STAGES` to build complete handler maps.

The constructor must validate that `task.status` and `initial_state.status` agree, that the state task ID matches, and that every `ACTIVE_STAGES` entry has a callable handler. `run()` must first move `PENDING -> QUEUED -> RUNNING`, or `QUEUED -> RUNNING`, using `transition_task()`. For each active stage, construct `StageInput` with the current snapshot, current step, attempt, fixed allowed tools, remaining model-call budget, and checkpoint context; call the handler with `self.call_tool`; merge `context_updates`; and advance only through `NEXT_STAGE` when `StageResult.completed` is true.

Use immutable domain snapshots rather than mutating `AnalysisTask` or `AgentState` in place:

```python
updated_task, event = transition_task(self._task, target)
self._task = updated_task
self._state = self._state.model_copy(
    update={
        "status": target,
        "events": (*self._state.events, event),
        "updated_at": event.occurred_at,
    }
)
```

`checkpoint()` must return the current version-1 `AgentCheckpoint`; `run()` must return `OrchestrationResult` with `status == state.status` and `task_id == checkpoint.task.task_id`. Add a `TaskEventType.ERROR` event for normalized stage errors in the next task, but keep status transition events in this task.

- [ ] **Step 4: Run the lifecycle tests to verify GREEN**

Run: `pytest tests/agent/test_orchestrator_lifecycle.py -q`

Expected: all lifecycle tests pass and the trace contains exactly six active-stage handler calls.

- [ ] **Step 5: Commit the lifecycle engine**

```powershell
git add src/data_analysis_agent/agent/orchestrator.py src/data_analysis_agent/agent/__init__.py tests/agent/test_orchestrator_lifecycle.py
git commit -m "feat: add fixed agent orchestration lifecycle"
```

### Task 3: Add retry, budget, timeout, and cancellation controls

**Files:**
- Create: `tests/agent/test_orchestrator_controls.py`
- Modify: `src/data_analysis_agent/agent/orchestrator.py`
- Modify: `src/data_analysis_agent/agent/orchestration_models.py`
- Modify: `src/data_analysis_agent/agent/orchestration_errors.py`

**Interfaces:**
- Consumes: Task 2 lifecycle loop and Task 1 `OrchestratorLimits`/`StageFailure`.
- Produces: stable result error codes `ORCHESTRATOR_STAGE_FAILED`, `ORCHESTRATOR_MAX_STEPS`, `ORCHESTRATOR_MAX_MODEL_CALLS`, `ORCHESTRATOR_TIMEOUT`, `ORCHESTRATOR_CANCELLED`, and `ORCHESTRATOR_INVALID_CHECKPOINT`; a thread-safe `cancel()` path; retry counters and elapsed-runtime accounting.

- [ ] **Step 1: Write failing control tests**

```python
from data_analysis_agent.agent.orchestration_models import (
    OrchestratorLimits,
    StageFailure,
    StageResult,
)
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import AnalysisTask


def test_retryable_stage_failure_retries_then_completes():
    calls = []
    task = AnalysisTask(query="retry")

    def flaky(stage_input, call_tool):
        calls.append(stage_input.attempt)
        if len(calls) == 1:
            return StageResult(
                completed=False,
                failure=StageFailure(
                    code="TEMPORARY_MODEL_ERROR",
                    message="retry offline",
                    retryable=True,
                ),
            )
        return StageResult(output={"ok": True})

    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}
    handlers[TaskStatus.EXPLORING] = flaky
    result = AgentOrchestrator(
        task=task,
        handlers=handlers,
        limits=OrchestratorLimits(max_stage_retries=1),
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert calls == [0, 1]
    assert any(event.event_type.value == "ERROR" for event in result.state.events)


def test_non_retryable_failure_enters_failed_without_advancing():
    calls = []
    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}

    def fail(stage_input, call_tool):
        calls.append(stage_input.stage)
        return StageResult(
            completed=False,
            failure=StageFailure(code="MODEL_SCHEMA_ERROR", message="bad shape"),
        )

    handlers[TaskStatus.ANALYZING] = fail
    result = AgentOrchestrator(task=AnalysisTask(query="fail"), handlers=handlers).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_STAGE_FAILED"
    assert calls == [TaskStatus.ANALYZING]


def test_max_steps_stops_a_non_completing_stage():
    calls = []
    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}

    def keep_running(stage_input, call_tool):
        calls.append(stage_input.step_number)
        return StageResult(completed=False)

    handlers[TaskStatus.ANALYZING] = keep_running
    result = AgentOrchestrator(
        task=AnalysisTask(query="budget"),
        handlers=handlers,
        limits=OrchestratorLimits(max_steps=2),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_MAX_STEPS"
    assert len(calls) == 2


def test_cancel_after_a_stage_prevents_the_next_stage():
    calls = []
    orchestrator = None

    def cancel_in_running(stage_input, call_tool):
        calls.append(stage_input.stage)
        orchestrator.cancel()
        return StageResult()

    handlers = {stage: lambda stage_input, call_tool: (calls.append(stage), StageResult())[1] for stage in AgentOrchestrator.ACTIVE_STAGES}
    handlers[TaskStatus.RUNNING] = cancel_in_running
    orchestrator = AgentOrchestrator(task=AnalysisTask(query="cancel"), handlers=handlers)

    result = orchestrator.run()

    assert result.status is TaskStatus.CANCELLED
    assert calls == [TaskStatus.RUNNING]
    assert result.error_code == "ORCHESTRATOR_CANCELLED"


def test_model_call_budget_is_checked_before_starting_a_new_stage():
    calls = []
    handlers = {stage: lambda stage_input, call_tool: (calls.append(stage), StageResult(model_calls=1))[1] for stage in AgentOrchestrator.ACTIVE_STAGES}

    result = AgentOrchestrator(
        task=AnalysisTask(query="model budget"),
        handlers=handlers,
        limits=OrchestratorLimits(max_model_calls=1),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_MAX_MODEL_CALLS"
    assert len(calls) == 1
```

Use a separate test with `monkeypatch` to replace `data_analysis_agent.agent.orchestrator.monotonic` with a deterministic clock whose second reading exceeds `max_runtime_seconds`; this avoids `sleep()`-based flakiness.

The deterministic timeout test must use this shape:

```python
def test_runtime_budget_stops_before_the_next_stage(monkeypatch):
    readings = iter([10.0, 10.0, 12.0])
    monkeypatch.setattr(
        "data_analysis_agent.agent.orchestrator.monotonic",
        lambda: next(readings),
    )
    handlers = {
        stage: lambda stage_input, call_tool: StageResult()
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }

    result = AgentOrchestrator(
        task=AnalysisTask(query="timeout"),
        handlers=handlers,
        limits=OrchestratorLimits(max_runtime_seconds=1.0),
    ).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_TIMEOUT"
```

- [ ] **Step 2: Run the control tests to verify RED**

Run: `pytest tests/agent/test_orchestrator_controls.py -q`

Expected: the tests fail because retry, budget, timeout, and cancellation handling are not implemented.

- [ ] **Step 3: Implement control handling**

Add a `threading.Event` for cancellation, `time.monotonic` measurement, and these private result helpers:

```python
def _terminal_failure(self, code: str, message: str) -> OrchestrationResult:
    if self._state.status not in TERMINAL_STATUSES:
        self._move(TaskStatus.FAILED, message=message, code=code)
    return self._result(error_code=code, error_message=message)


def _cancelled_result(self) -> OrchestrationResult:
    if self._state.status not in TERMINAL_STATUSES:
        self._move(TaskStatus.CANCELLED, message="orchestration cancelled", code="ORCHESTRATOR_CANCELLED")
    return self._result(
        error_code="ORCHESTRATOR_CANCELLED",
        error_message="orchestration cancelled",
    )
```

Before each handler call, check the cancel event, total step count, total model-call count, and accumulated runtime. After each call, add the measured duration to `checkpoint.elapsed_runtime_seconds`; reject `StageResult.model_calls > remaining_model_calls`; increment the stage attempt only for a retry; and append an `ERROR` event with safe metadata containing `stage`, `step_number`, `attempt`, and `cause_code`. A retryable `StageFailure` is retried while `attempt < max_stage_retries`; every other failure or an exhausted retry count transitions to `FAILED`.

The first active stage may run with zero prior model calls. Once the model-call budget is exhausted, do not invoke another handler; return `ORCHESTRATOR_MAX_MODEL_CALLS`. This intentionally makes a report requiring another model call fail cleanly instead of silently exceeding the budget.

- [ ] **Step 4: Run the control tests and the lifecycle regression**

Run: `pytest tests/agent/test_orchestrator_controls.py tests/agent/test_orchestrator_lifecycle.py -q`

Expected: all control and lifecycle tests pass.

- [ ] **Step 5: Commit the control behavior**

```powershell
git add src/data_analysis_agent/agent/orchestrator.py src/data_analysis_agent/agent/orchestration_models.py src/data_analysis_agent/agent/orchestration_errors.py tests/agent/test_orchestrator_controls.py
git commit -m "feat: add orchestration budgets retries and cancellation"
```

### Task 4: Enforce stage tool policy and record tool events

**Files:**
- Create: `tests/agent/test_orchestrator_tools.py`
- Modify: `src/data_analysis_agent/agent/orchestrator.py`

**Interfaces:**
- Consumes: existing `ToolRegistry`, `ToolDefinition`, `ToolExecutor`, `ToolContext`, `ToolCallRequest`, `ToolCallResult`, and Task 2 `STAGE_ALLOWED_TOOLS`.
- Produces: `call_tool()` stage allow-list enforcement, `ORCHESTRATOR_TOOL_NOT_ALLOWED`, `ORCHESTRATOR_TOOL_UNAVAILABLE`, and task-correlated `TOOL_CALLED` events/state records.

- [ ] **Step 1: Write failing tool-policy tests**

```python
from pydantic import BaseModel
from uuid import uuid4

from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import TaskStatus, ToolCallStatus
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.tools import ToolDefinition, ToolExecutor, ToolRegistry, ToolRiskLevel


class Input(BaseModel):
    value: int


class Output(BaseModel):
    result: int


def _registry(seen):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="validate_metric",
            description="test metric",
            input_model=Input,
            output_model=Output,
            side_effect=False,
            network_access=False,
            max_runtime_seconds=1,
            required_permissions=frozenset(),
            risk_level=ToolRiskLevel.LOW,
        ),
        lambda value, context: (seen.append((value.value, context.task_id)), {"result": value.value}),
    )
    return registry


def test_allowed_tool_is_executed_with_the_orchestrator_task_id():
    seen = []
    task = AnalysisTask(query="tool")
    executor = ToolExecutor(_registry(seen))
    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}

    def validate(stage_input, call_tool):
        result = call_tool("validate_metric", {"value": 7})
        assert result.status is ToolCallStatus.SUCCEEDED
        return StageResult(output={"tool_result": result.output})

    handlers[TaskStatus.ANALYZING] = validate
    result = AgentOrchestrator(task=task, handlers=handlers, tool_executor=executor).run()

    assert result.status is TaskStatus.COMPLETED
    assert seen == [(7, task.task_id)]
    assert any(event.event_type.value == "TOOL_CALLED" for event in result.state.events)


def test_tool_not_allowed_for_the_current_stage_never_reaches_the_handler():
    seen = []
    task = AnalysisTask(query="policy")
    executor = ToolExecutor(_registry(seen))
    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}

    def explore(stage_input, call_tool):
        result = call_tool("validate_metric", {"value": 7})
        assert result.status is ToolCallStatus.FAILED
        assert result.error_code == "ORCHESTRATOR_TOOL_NOT_ALLOWED"
        return StageResult(completed=False, failure=StageFailure(code=result.error_code, message=result.error_message or "denied"))

    handlers[TaskStatus.EXPLORING] = explore
    result = AgentOrchestrator(task=task, handlers=handlers, tool_executor=executor).run()

    assert result.status is TaskStatus.FAILED
    assert seen == []


def test_tool_request_without_executor_returns_a_stable_failure():
    task = AnalysisTask(query="no executor")
    handlers = {stage: lambda stage_input, call_tool: StageResult() for stage in AgentOrchestrator.ACTIVE_STAGES}

    def analyze(stage_input, call_tool):
        result = call_tool("run_sql", {"query": "select 1", "parameters": {}})
        assert result.status is ToolCallStatus.FAILED
        assert result.error_code == "ORCHESTRATOR_TOOL_UNAVAILABLE"
        return StageResult(completed=False, failure=StageFailure(code=result.error_code, message=result.error_message or "unavailable"))

    handlers[TaskStatus.ANALYZING] = analyze
    result = AgentOrchestrator(task=task, handlers=handlers).run()

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_STAGE_FAILED"
```

Import `StageFailure` and `StageResult` in the test file. The first run must fail because `call_tool()` currently has no stage policy or result recording.

- [ ] **Step 2: Run the tool tests to verify RED**

Run: `pytest tests/agent/test_orchestrator_tools.py -q`

Expected: failures show that allowed tools are not dispatched and policy error codes are missing.

- [ ] **Step 3: Implement the tool bridge**

Implement `call_tool()` with this order:

```python
if self._cancel_event.is_set():
    return self._failed_tool_result("ORCHESTRATOR_CANCELLED", "orchestration cancelled")
if tool_name not in STAGE_ALLOWED_TOOLS[self._state.status]:
    return self._failed_tool_result("ORCHESTRATOR_TOOL_NOT_ALLOWED", "tool is not allowed in the current stage")
if self._tool_executor is None:
    return self._failed_tool_result("ORCHESTRATOR_TOOL_UNAVAILABLE", "tool executor is not configured")

request = ToolCallRequest(
    task_id=self._task.task_id,
    tool_name=tool_name,
    arguments=dict(arguments),
)
context = self._tool_context_factory(self._task.task_id)
result = self._tool_executor.execute(request, context)
self._record_tool_result(result)
return result
```

Use a default context factory returning `ToolContext(task_id=task_id)` when no factory is supplied. `_record_tool_result()` must append a `TaskEventType.TOOL_CALLED` event whose metadata contains only `tool_name`, `call_id`, `status`, `error_code`, and `duration_ms`; it must also append a domain `ToolCall` snapshot to `AgentState.tool_calls` without storing raw secrets. Never invoke `ToolExecutor` for a disallowed or unavailable tool.

- [ ] **Step 4: Run tool tests and existing tool regressions**

Run: `pytest tests/agent/test_orchestrator_tools.py tests/tools -q`

Expected: all new tool-policy tests and all existing M07 tool tests pass.

- [ ] **Step 5: Commit the tool boundary**

```powershell
git add src/data_analysis_agent/agent/orchestrator.py tests/agent/test_orchestrator_tools.py
git commit -m "feat: enforce stage tool policies in orchestrator"
```

### Task 5: Add checkpoint recovery and report idempotence

**Files:**
- Create: `tests/agent/test_orchestrator_checkpoint.py`
- Modify: `src/data_analysis_agent/agent/orchestrator.py`
- Modify: `src/data_analysis_agent/agent/orchestration_models.py`

**Interfaces:**
- Consumes: `AgentCheckpoint`, `AgentState.report_artifacts`, `report_generated`, and `OrchestrationResult.output`.
- Produces: validated checkpoint resume from active stages, direct terminal resume, persisted output/context, and at-most-once report-stage execution.

- [ ] **Step 1: Write failing recovery/idempotence tests**

```python
import pytest

from data_analysis_agent.agent.orchestration_models import AgentCheckpoint, StageResult
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.domain.enums import ReportFormat, TaskStatus
from data_analysis_agent.domain.models import AgentState, AnalysisTask, ReportArtifact


def test_resume_from_exploring_does_not_run_running_or_initialization_handlers():
    task = AnalysisTask(query="resume")
    state = AgentState(task_id=task.task_id, status=TaskStatus.EXPLORING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.EXPLORING}),
        state=state,
        step_number=2,
        context={"profile": {"rows": 2}},
    )
    trace = []
    handlers = {
        stage: (lambda stage_input, call_tool, stage=stage: (trace.append(stage), StageResult())[1])
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }

    result = AgentOrchestrator(task=task, handlers=handlers).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert TaskStatus.RUNNING not in trace
    assert TaskStatus.EXPLORING in trace
    assert result.checkpoint.context["profile"]["rows"] == 2


def test_completed_checkpoint_returns_saved_output_without_calling_handlers():
    task = AnalysisTask(query="completed")
    state = AgentState(task_id=task.task_id, status=TaskStatus.COMPLETED)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.COMPLETED}),
        state=state,
        step_number=8,
        output={"final_report": "# saved"},
    )
    calls = []
    handlers = {stage: lambda stage_input, call_tool: (calls.append(stage), StageResult())[1] for stage in AgentOrchestrator.ACTIVE_STAGES}

    result = AgentOrchestrator(task=task, handlers=handlers).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert result.output == {"final_report": "# saved"}
    assert calls == []


def test_existing_report_artifact_skips_report_handler():
    task = AnalysisTask(query="report once")
    report = ReportArtifact(format=ReportFormat.MARKDOWN, file_path="reports/report.md")
    state = AgentState(
        task_id=task.task_id,
        status=TaskStatus.REPORTING,
        report_artifacts=(report,),
    )
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.REPORTING}),
        state=state,
        step_number=7,
        output={"final_report": "# existing"},
    )
    calls = []
    handlers = {stage: lambda stage_input, call_tool: (calls.append(stage), StageResult())[1] for stage in AgentOrchestrator.ACTIVE_STAGES}

    result = AgentOrchestrator(task=task, handlers=handlers).run(checkpoint=checkpoint)

    assert result.status is TaskStatus.COMPLETED
    assert calls.count(TaskStatus.REPORTING) == 0
    assert result.output["final_report"] == "# existing"
```

Add tests for a mismatched checkpoint task ID, a task/state status mismatch, and an unsupported checkpoint version; each must return `ORCHESTRATOR_INVALID_CHECKPOINT` without invoking a handler.

Use this parameterized test for those invalid snapshots:

```python
@pytest.mark.parametrize(
    "mutate",
    [
        lambda checkpoint: checkpoint.model_copy(update={"version": 2}),
        lambda checkpoint: checkpoint.model_copy(update={"task": AnalysisTask(query="other")}),
    ],
)
def test_invalid_checkpoint_is_reported_without_handler_calls(mutate):
    task = AnalysisTask(query="resume validation")
    state = AgentState(task_id=task.task_id, status=TaskStatus.EXPLORING)
    checkpoint = AgentCheckpoint(
        task=task.model_copy(update={"status": TaskStatus.EXPLORING}),
        state=state,
        step_number=2,
    )
    calls = []
    handlers = {
        stage: lambda stage_input, call_tool: (calls.append(stage), StageResult())[1]
        for stage in AgentOrchestrator.ACTIVE_STAGES
    }

    result = AgentOrchestrator(task=task, handlers=handlers).run(
        checkpoint=mutate(checkpoint)
    )

    assert result.status is TaskStatus.FAILED
    assert result.error_code == "ORCHESTRATOR_INVALID_CHECKPOINT"
    assert calls == []
```

- [ ] **Step 2: Run the recovery tests to verify RED**

Run: `pytest tests/agent/test_orchestrator_checkpoint.py -q`

Expected: failures show that `run(checkpoint=...)` either restarts from `PENDING` or invokes the report handler more than once.

- [ ] **Step 3: Implement checkpoint validation and idempotent reporting**

At the start of `run(checkpoint=...)`, validate version, task ID, task/state status equality, active/terminal status shape, non-negative counters, and JSON-safe context. Copy the checkpoint into the orchestrator only after all checks pass. Use the checkpoint’s `output` as the initial result payload.

Before invoking a `REPORTING` handler, skip it when either `checkpoint.report_generated` is true or `state.report_artifacts` is non-empty. On a skipped report, move directly to `COMPLETED`. On a successful report handler result, copy its mapping output into checkpoint output and set `report_generated=True` before moving to `COMPLETED`; if the handler fails, leave the flag false. A `COMPLETED` checkpoint must return immediately and a `FAILED`/`CANCELLED` checkpoint must return immediately without automatic retry.

- [ ] **Step 4: Run checkpoint tests and the full new agent suite**

Run: `pytest tests/agent -q`

Expected: all model, lifecycle, control, tool, and checkpoint tests pass.

- [ ] **Step 5: Commit recovery and report idempotence**

```powershell
git add src/data_analysis_agent/agent/orchestrator.py src/data_analysis_agent/agent/orchestration_models.py tests/agent/test_orchestrator_checkpoint.py
git commit -m "feat: add orchestration checkpoint recovery"
```

### Task 6: Implement the legacy analysis adapter

**Files:**
- Create: `src/data_analysis_agent/agent/legacy_adapter.py`
- Create: `tests/agent/test_legacy_adapter.py`
- Create: `tests/fixtures/recording_executor.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`

**Interfaces:**
- Consumes: existing `DataAnalysisAgent` methods `_request_structured_action()`, `_process_action()`, `_generate_final_report()`, `_build_conversation_prompt()`, `analysis_results`, `conversation_history`, `current_round`, `max_rounds`, and existing dataset/session context.
- Produces: `LegacyAnalysisAdapter(agent, user_input, dataset_context, max_rounds)`, `handlers()`, and `to_legacy_result(OrchestrationResult)`.

- [ ] **Step 1: Write failing adapter tests with the existing `FakeLLM` and a recording executor**

```python
from data_analysis_agent.agent.legacy_adapter import LegacyAnalysisAdapter
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.agent.core import DataAnalysisAgent
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from tests.fixtures.recording_executor import RecordingExecutor


def test_adapter_keeps_execution_failure_as_feedback_for_the_next_model_step(tmp_path, monkeypatch):
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="raise ValueError('planned failure')"),
        yaml_response("generate_code", code="value = 2 + 3"),
        yaml_response("analysis_complete", final_report="# analysis marker"),
        yaml_response("analysis_complete", final_report="# final report"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    agent = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=5,
        generate_word_report=False,
    )
    agent.session_output_dir = str(tmp_path)
    agent.executor = RecordingExecutor()
    agent.conversation_history = [{"role": "user", "content": "offline"}]
    agent.analysis_results = []
    agent.current_round = 0

    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=5,
    )
    handlers = adapter.handlers()
    orchestrator = AgentOrchestrator(
        task=adapter.task,
        handlers=handlers,
        limits=adapter.limits,
    )

    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert len(agent.analysis_results) == 2
    assert agent.analysis_results[0]["result"]["success"] is False
    assert "planned failure" in agent.conversation_history[-1]["content"]


def test_adapter_maps_report_generation_to_one_reporting_stage_call(monkeypatch):
    calls = []
    agent = object.__new__(DataAnalysisAgent)
    agent.analysis_results = []
    agent.conversation_history = []
    agent.current_round = 0
    agent.generate_word_report = False
    agent._generate_final_report = lambda: calls.append("report") or {"final_report": "# done"}
    adapter = LegacyAnalysisAdapter(agent=agent, user_input="offline", dataset_context=[], max_rounds=0)

    result = AgentOrchestrator(
        task=adapter.task,
        handlers=adapter.handlers(),
        limits=adapter.limits,
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert calls == ["report"]
    assert result.output["final_report"] == "# done"
```

Create `tests/fixtures/recording_executor.py` with this deterministic executor double so adapter tests and the public compatibility test use the same behavior:

```python
class RecordingExecutor:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir
        self.calls = []

    def execute_code(self, code):
        self.calls.append(code)
        if "raise ValueError" in code:
            return {
                "success": False,
                "output": "",
                "error": "planned executor failure",
                "variables": {},
            }
        return {
            "success": True,
            "output": "5",
            "error": "",
            "variables": {},
        }

    def set_variable(self, name, value):
        return None

    def set_sensitive_columns(self, names):
        return None

    def get_environment_info(self):
        return "offline executor"

    def reset_environment(self):
        self.calls.clear()
```

Add a test that `max_rounds=0` skips analysis model calls but still invokes the report handler once, preserving the existing compatibility contract.

The zero-round regression test must assert both call counts:

```python
def test_adapter_zero_rounds_skips_analysis_but_generates_report(monkeypatch):
    fake_llm = FakeLLM([yaml_response("analysis_complete", final_report="# report")])
    agent = object.__new__(DataAnalysisAgent)
    agent.llm = fake_llm
    agent.analysis_results = []
    agent.conversation_history = [{"role": "user", "content": "offline"}]
    agent.current_round = 0
    agent.generate_word_report = False
    agent._generate_final_report = lambda: {"final_report": "# report"}

    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=0,
    )
    result = AgentOrchestrator(
        task=adapter.task,
        handlers=adapter.handlers(),
        limits=adapter.limits,
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert fake_llm.calls == []
    assert result.output["final_report"] == "# report"
```

- [ ] **Step 2: Run adapter tests to verify RED**

Run: `pytest tests/agent/test_legacy_adapter.py -q`

Expected: collection fails because `legacy_adapter.py` does not exist.

- [ ] **Step 3: Implement stage handlers around existing agent behavior**

Implement the adapter with the following handler mapping:

```python
class LegacyAnalysisAdapter:
    def __init__(self, *, agent, user_input, dataset_context, max_rounds):
        self.agent = agent
        self.user_input = user_input
        self.dataset_context = tuple(dataset_context)
        self.max_rounds = max_rounds
        self.task = AnalysisTask(
            query=user_input,
            dataset_ids=tuple(
                UUID(item["dataset_id"])
                for item in self.dataset_context
                if item.get("dataset_id")
            ),
            max_rounds=max(1, max_rounds),
        )
        self.limits = OrchestratorLimits(
            max_steps=max(6, max_rounds + 6),
            max_model_calls=max(1, max_rounds) + 1,
        )

    def handlers(self):
        return {
            TaskStatus.RUNNING: self._initialize,
            TaskStatus.EXPLORING: self._explore,
            TaskStatus.CLEANING: self._clean,
            TaskStatus.ANALYZING: self._analyze_step,
            TaskStatus.VALIDATING: self._validate,
            TaskStatus.REPORTING: self._report,
        }
```

`_initialize`, `_explore`, `_clean`, and `_validate` return `StageResult()` unless the existing agent context has a concrete validation error. `_analyze_step` must stop before making an LLM call when `agent.current_round >= max_rounds`; otherwise increment `current_round`, call `_request_structured_action()` using the existing conversation prompt/system prompt, serialize the typed action, call `_process_action()`, and append the same assistant/user feedback and `analysis_results` records currently produced by `_analyze_impl()`. A successful `generate_code` or `collect_figures` result returns `completed=False` until the round limit; `analysis_complete` returns `completed=True` without treating its embedded report as the final report.

The adapter must preserve execution failures as ordinary `generate_code` results and feedback, so the next analysis step can see `代码执行失败` and the executor error. Model/schema failures return a non-retryable `StageFailure` with a stable cause code; do not expose raw API credentials or absolute paths.

`_report` must call `agent._generate_final_report()` exactly once and return its JSON-safe result mapping. `to_legacy_result()` returns the stored report output merged with `total_rounds`, `analysis_results`, `collected_figures`, `conversation_history`, `task_id`, artifact records, download URLs, and storage error fields. Convert Pydantic/domain objects to `model_dump(mode="json")` before placing them in orchestration output, while keeping the existing direct return values expected by current tests.

- [ ] **Step 4: Run adapter tests and existing contract tests**

Run: `pytest tests/agent/test_legacy_adapter.py tests/contract/test_agent_contract.py -q`

Expected: adapter tests and all existing Agent contract tests pass.

- [ ] **Step 5: Commit the compatibility adapter**

```powershell
git add src/data_analysis_agent/agent/legacy_adapter.py src/data_analysis_agent/agent/__init__.py tests/agent/test_legacy_adapter.py tests/fixtures/recording_executor.py
git commit -m "feat: adapt legacy agent behavior to orchestration stages"
```

### Task 7: Route `DataAnalysisAgent` through the orchestrator and preserve public APIs

**Files:**
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`
- Modify: `src/data_analysis_agent/cli.py` only if module imports require an explicit export
- Create: `tests/agent/test_agent_compatibility.py`

**Interfaces:**
- Consumes: Task 6 `LegacyAnalysisAdapter`, Task 2-5 `AgentOrchestrator`, and the existing dataset/session setup in `_analyze_impl()`.
- Produces: the same `DataAnalysisAgent.analyze()` and `quick_analysis()` signatures and result keys, with orchestration state available through `agent.orchestrator` for worker/API callers.

- [ ] **Step 1: Write failing compatibility tests**

```python
import data_analysis_agent as package
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.domain.enums import TaskStatus
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from tests.fixtures.recording_executor import RecordingExecutor


def test_analyze_uses_orchestrator_and_keeps_legacy_result_shape(tmp_path, monkeypatch):
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="value = 5"),
        yaml_response("analysis_complete", final_report="# analysis marker"),
        yaml_response("analysis_complete", final_report="# final report"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", RecordingExecutor)

    agent = package.DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=2,
        generate_word_report=False,
    )
    result = agent.analyze("offline")

    assert result["final_report"] == "# final report"
    assert result["total_rounds"] == 2
    assert agent.orchestrator.checkpoint().state.status is TaskStatus.COMPLETED


def test_quick_analysis_still_hides_compatibility_upload_paths(tmp_path, monkeypatch):
    source = tmp_path / "private.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="df = load_dataset(dataset_ids[0])"),
        yaml_response("analysis_complete", final_report="# marker"),
        yaml_response("analysis_complete", final_report="# final"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    result = package.quick_analysis(
        "private data",
        files=[str(source)],
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
    )

    assert result["final_report"] == "# final"
    assert str(source) not in "\n".join(call.prompt for call in fake_llm.calls)
```

Import the shared deterministic `RecordingExecutor` from `tests/fixtures/recording_executor.py`; do not duplicate an executor implementation with different behavior in this compatibility test.

- [ ] **Step 2: Run compatibility tests to verify RED**

Run: `pytest tests/agent/test_agent_compatibility.py -q`

Expected: the tests fail because `_analyze_impl()` still owns the old `while` loop and `DataAnalysisAgent.orchestrator` is not assigned.

- [ ] **Step 3: Replace only the old execution loop with the adapter/orchestrator call**

Keep the following existing responsibilities in `_analyze_impl()` unchanged: file/dataset exclusivity validation, dataset profile lookup, sensitive-column registration, session directory creation, executor setup, dataset loader registration, and initial context construction.

Replace the block beginning at the old `while self.current_round < self.max_rounds:` and ending at the direct `_generate_final_report()` return with:

```python
adapter = LegacyAnalysisAdapter(
    agent=self,
    user_input=user_input,
    dataset_context=dataset_context,
    max_rounds=self.max_rounds,
)
self.orchestrator = AgentOrchestrator(
    task=adapter.task,
    handlers=adapter.handlers(),
    limits=adapter.limits,
    tool_executor=self.tool_executor,
    tool_context_factory=lambda task_id: ToolContext(
        task_id=task_id,
        user_id=self.dataset_owner_id,
    ),
)
orchestration_result = self.orchestrator.run()
if orchestration_result.status is not TaskStatus.COMPLETED:
    self.cleanup_storage_outputs()
    raise AgentOrchestrationError(
        orchestration_result.error_code or "ORCHESTRATOR_FAILED",
        orchestration_result.error_message or "analysis orchestration failed",
    )
return adapter.to_legacy_result(orchestration_result)
```

Initialize `self.orchestrator = None` in `DataAnalysisAgent.__init__`. Keep `_process_response()` and `_process_action()` intact for M00-M07 direct contracts. Preserve the existing `analyze()` recursion/LLM close behavior and the `files` compatibility context manager. Import `TaskStatus`, `AgentOrchestrationError`, `AgentOrchestrator`, `LegacyAnalysisAdapter`, and `ToolContext` explicitly instead of mixing relative and root-package imports.

For `max_rounds=0`, construct the domain task with `max(1, max_rounds)` inside the adapter and let `_analyze_step` return completed before a model call; the report stage still performs its single final report call. This preserves the existing upload cleanup tests.

- [ ] **Step 4: Run the focused compatibility and integration tests**

Run: `pytest tests/agent tests/contract/test_agent_contract.py tests/integration/test_analysis_flow.py tests/integration/test_dataset_analysis_flow.py -q`

Expected: all new orchestration/compatibility tests and existing offline analysis flows pass without an API key.

- [ ] **Step 5: Commit the public-entry migration**

```powershell
git add src/data_analysis_agent/agent/core.py src/data_analysis_agent/agent/__init__.py src/data_analysis_agent/__init__.py tests/agent/test_agent_compatibility.py
git commit -m "feat: route legacy analysis through orchestrator"
```

### Task 8: Complete exports, documentation, and regression verification

**Files:**
- Modify: `src/data_analysis_agent/__init__.py`
- Modify: `README.md` only for the new module entry point and orchestration overview
- Create: `tests/agent/test_public_api.py`

**Interfaces:**
- Consumes: all M08 public classes from Tasks 1-7.
- Produces: one stable import path for `AgentOrchestrator`, orchestration models, and orchestration errors; documented module invocation; full regression evidence.

- [ ] **Step 1: Write the failing public API tests**

```python
def test_orchestrator_public_symbols_are_exported_from_the_package():
    from data_analysis_agent import (
        AgentCheckpoint,
        AgentOrchestrator,
        OrchestrationResult,
        OrchestratorLimits,
        StageFailure,
        StageInput,
        StageResult,
    )

    assert AgentOrchestrator.__name__ == "AgentOrchestrator"
    assert AgentCheckpoint.__name__ == "AgentCheckpoint"
    assert OrchestrationResult.__name__ == "OrchestrationResult"
    assert OrchestratorLimits.__name__ == "OrchestratorLimits"
    assert StageFailure.__name__ == "StageFailure"
    assert StageInput.__name__ == "StageInput"
    assert StageResult.__name__ == "StageResult"


def test_module_entry_point_is_importable_without_api_key(monkeypatch):
    import importlib

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    module = importlib.import_module("data_analysis_agent.__main__")
    assert callable(module.main)
```

- [ ] **Step 2: Run the public API tests to verify RED**

Run: `pytest tests/agent/test_public_api.py -q`

Expected: the new symbols are not yet exported from the root package.

- [ ] **Step 3: Add exports and concise module documentation**

Export the following from `src/data_analysis_agent/__init__.py` and include them in `__all__`: `AgentOrchestrator`, `AgentCheckpoint`, `OrchestrationResult`, `OrchestratorLimits`, `StageFailure`, `StageInput`, `StageResult`, and `AgentOrchestrationError`.

Add a README section with the exact offline invocation:

```powershell
python -m data_analysis_agent --help
pytest tests/agent -q
```

State that `DataAnalysisAgent` remains the compatibility facade and that production callers can use `AgentOrchestrator` with typed handlers/checkpoints. Do not document API keys, real provider calls, or a new persistence requirement for M08.

- [ ] **Step 4: Run the complete verification matrix**

Run each command and record its exit code and test count:

```powershell
pytest tests/agent -q
pytest tests/contract tests/integration tests/llm tests/tools tests/storage tests/database tests/domain tests/api tests/config tests/public_api tests/persistence -q
python -m data_analysis_agent --help
python -c "from data_analysis_agent import AgentOrchestrator, quick_analysis; print(AgentOrchestrator.__name__, callable(quick_analysis))"
```

Expected: all commands exit 0; no command attempts a real model API; the module help command is importable without `OPENAI_API_KEY`.

- [ ] **Step 5: Review requirements against the implementation and commit**

Check each item before committing: fixed stage order, illegal transition rejection, terminal state, bounded retries, step/model/time budgets, cancellation, checkpoint resume, report at-most-once, tool allow-list, legacy result shape, `quick_analysis()`, no API key requirement for tests, and module-level imports.

```powershell
git diff --check
git status --short --branch
git add README.md src/data_analysis_agent/__init__.py tests/agent/test_public_api.py
git commit -m "docs: expose M08 orchestration public API"
```

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-22-m08-agent-state-machine.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task and review between tasks.
2. **Inline Execution** — execute the tasks in this session with checkpoints.

Choose an execution approach before production code is changed.
