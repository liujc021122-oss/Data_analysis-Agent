from collections.abc import Callable, Mapping
from threading import Event
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from ..domain.enums import TaskEventType, TaskStatus, ToolCallStatus
from ..domain.models import AgentState, AnalysisTask, TaskEvent, ToolCall, utc_now
from ..domain.state import transition_task
from ..tools import ToolAuditRecord, ToolCallRequest, ToolCallResult, ToolContext
from .orchestration_models import (
    AgentCheckpoint,
    OrchestrationResult,
    OrchestratorLimits,
    StageHandler,
    StageInput,
)


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


class AgentOrchestrator:
    ACTIVE_STAGES = ACTIVE_STAGES
    TERMINAL_STATUSES = TERMINAL_STATUSES
    NEXT_STAGE = NEXT_STAGE
    STAGE_ALLOWED_TOOLS = STAGE_ALLOWED_TOOLS

    def __init__(
        self,
        *,
        task: AnalysisTask,
        handlers: Mapping[TaskStatus, StageHandler],
        limits: OrchestratorLimits | None = None,
        tool_executor: Any = None,
        tool_context_factory: Callable[[UUID], ToolContext] | None = None,
        initial_state: AgentState | None = None,
    ) -> None:
        state = (
            initial_state
            if initial_state is not None
            else AgentState(task_id=task.task_id, status=task.status)
        )
        if state.task_id != task.task_id:
            raise ValueError("initial state task ID must match task ID")
        if state.status is not task.status:
            raise ValueError("task and initial state status must match")
        for stage in ACTIVE_STAGES:
            handler = handlers.get(stage)
            if not callable(handler):
                raise ValueError(f"missing callable handler for {stage.value}")

        self._task = task
        self._state = state
        self._handlers = dict(handlers)
        self._limits = limits or OrchestratorLimits()
        self._tool_executor = tool_executor
        self._tool_context_factory = tool_context_factory or self._default_tool_context
        self._step_number = 0
        self._stage_attempts: dict[str, int] = {}
        self._context = dict(state.context)
        self._output: dict[str, Any] = {}
        self._report_generated = False
        self._cancel_event = Event()
        self._model_calls = task.model_call_count
        self._elapsed_runtime = 0.0
        self._runtime_last_reading: float | None = None

    def run(self) -> OrchestrationResult:
        self._runtime_last_reading = monotonic()
        if self._task.status is TaskStatus.PENDING:
            self._transition(TaskStatus.QUEUED)
        if self._task.status is TaskStatus.QUEUED:
            self._transition(TaskStatus.RUNNING)

        while self._task.status in ACTIVE_STAGES:
            if self._cancel_event.is_set():
                return self._cancelled_result()
            if self._step_number >= self._limits.max_steps:
                return self._terminal_failure(
                    "ORCHESTRATOR_MAX_STEPS", "maximum orchestration steps exceeded"
                )
            if self._step_number:
                self._refresh_runtime()
            if self._elapsed_runtime > self._limits.max_runtime_seconds:
                return self._terminal_failure(
                    "ORCHESTRATOR_TIMEOUT", "orchestration runtime limit exceeded"
                )
            remaining_model_calls = self._limits.max_model_calls - self._model_calls
            if remaining_model_calls <= 0:
                return self._terminal_failure(
                    "ORCHESTRATOR_MAX_MODEL_CALLS",
                    "maximum model calls exceeded",
                )
            stage = self._task.status
            attempt = self._stage_attempts.get(stage.value, 0)
            stage_input = StageInput(
                task=self._task,
                state=self._state,
                stage=stage,
                step_number=self._step_number,
                attempt=attempt,
                allowed_tools=STAGE_ALLOWED_TOOLS[stage],
                remaining_model_calls=remaining_model_calls,
                context=dict(self._context),
            )
            try:
                result = self._handlers[stage](stage_input, self.call_tool)
            except Exception:
                self._refresh_runtime()
                self._append_error_event(
                    stage,
                    step_number=self._step_number,
                    attempt=attempt,
                    cause_code="STAGE_EXECUTION_ERROR",
                )
                self._step_number += 1
                return self._terminal_failure(
                    "ORCHESTRATOR_STAGE_FAILED", "stage execution failed"
                )
            self._refresh_runtime()
            if result.model_calls > remaining_model_calls:
                self._append_error_event(
                    stage,
                    step_number=self._step_number,
                    attempt=attempt,
                    cause_code="ORCHESTRATOR_MAX_MODEL_CALLS",
                )
                self._step_number += 1
                return self._terminal_failure(
                    "ORCHESTRATOR_MAX_MODEL_CALLS",
                    "maximum model calls exceeded",
                )
            if result.model_calls:
                self._model_calls += result.model_calls
                self._task = self._task.model_copy(
                    update={"model_call_count": self._model_calls}
                )
            self._context.update(result.context_updates)
            if result.context_updates:
                self._state = self._state.model_copy(
                    update={"context": dict(self._context), "updated_at": utc_now()}
                )
            if result.output is not None:
                self._output[stage.value] = result.output
            if result.failure is not None:
                self._append_error_event(
                    stage,
                    step_number=self._step_number,
                    attempt=attempt,
                    cause_code=result.failure.code,
                )
                self._step_number += 1
                if (
                    result.failure.retryable
                    and attempt < self._limits.max_stage_retries
                ):
                    self._stage_attempts[stage.value] = attempt + 1
                    continue
                return self._terminal_failure(
                    "ORCHESTRATOR_STAGE_FAILED", "stage execution failed"
                )
            self._step_number += 1
            if not result.completed:
                continue
            if stage is TaskStatus.REPORTING:
                self._report_generated = True
            self._transition(NEXT_STAGE[stage])

        checkpoint = self.checkpoint()
        return OrchestrationResult(
            task_id=checkpoint.task.task_id,
            status=self._state.status,
            state=self._state,
            output=dict(self._output),
            checkpoint=checkpoint,
        )

    def cancel(self) -> None:
        self._cancel_event.set()

    def checkpoint(self) -> AgentCheckpoint:
        return AgentCheckpoint(
            task=self._task,
            state=self._state,
            step_number=self._step_number,
            stage_attempts=dict(self._stage_attempts),
            report_generated=self._report_generated,
            elapsed_runtime_seconds=self._elapsed_runtime,
            context=dict(self._context),
            output=dict(self._output),
        )

    def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        if self._cancel_event.is_set():
            return self._failed_tool_result(
                tool_name, "ORCHESTRATOR_CANCELLED", "orchestration cancelled"
            )
        if tool_name not in STAGE_ALLOWED_TOOLS[self._state.status]:
            return self._failed_tool_result(
                tool_name,
                "ORCHESTRATOR_TOOL_NOT_ALLOWED",
                "tool is not allowed in the current stage",
            )
        if self._tool_executor is None:
            return self._failed_tool_result(
                tool_name,
                "ORCHESTRATOR_TOOL_UNAVAILABLE",
                "tool executor is not configured",
            )

        request = ToolCallRequest(
            task_id=self._task.task_id,
            tool_name=tool_name,
            arguments=dict(arguments),
        )
        context = self._tool_context_factory(self._task.task_id)
        result = self._tool_executor.execute(request, context)
        self._record_tool_result(request, result)
        return result

    @staticmethod
    def _default_tool_context(task_id: UUID) -> ToolContext:
        return ToolContext(task_id=task_id)

    def _failed_tool_result(
        self, tool_name: str, error_code: str, error_message: str
    ) -> ToolCallResult:
        occurred_at = utc_now()
        return ToolCallResult(
            call_id=uuid4(),
            task_id=self._task.task_id,
            tool_name=tool_name,
            status=ToolCallStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            started_at=occurred_at,
            finished_at=occurred_at,
            duration_ms=0,
        )

    def _record_tool_result(
        self, request: ToolCallRequest, result: ToolCallResult
    ) -> None:
        snapshot = ToolAuditRecord(
            call_id=result.call_id,
            task_id=result.task_id,
            tool_name=result.tool_name,
            status=result.status,
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_ms=result.duration_ms,
            arguments=request.arguments,
            output=result.output,
            error_code=result.error_code,
            error_message=result.error_message,
        )
        event = TaskEvent(
            task_id=self._task.task_id,
            event_type=TaskEventType.TOOL_CALLED,
            to_status=self._state.status,
            metadata={
                "tool_name": result.tool_name,
                "call_id": str(result.call_id),
                "status": result.status.value,
                "error_code": result.error_code,
                "duration_ms": result.duration_ms,
            },
        )
        call = ToolCall(
            tool_call_id=result.call_id,
            task_id=result.task_id,
            tool_name=result.tool_name,
            arguments=dict(snapshot.arguments),
            result=snapshot.output,
            status=result.status,
            started_at=result.started_at,
            finished_at=result.finished_at,
            error_message=snapshot.error_message,
        )
        self._state = self._state.model_copy(
            update={
                "events": (*self._state.events, event),
                "tool_calls": (*self._state.tool_calls, call),
                "updated_at": event.occurred_at,
            }
        )

    def _transition(
        self,
        target: TaskStatus,
        *,
        message: str | None = None,
        code: str | None = None,
    ) -> None:
        updated_task, event = transition_task(self._task, target, message=message)
        if code is not None:
            event = event.model_copy(update={"metadata": {"code": code}})
        self._task = updated_task
        self._state = self._state.model_copy(
            update={
                "status": target,
                "events": (*self._state.events, event),
                "updated_at": event.occurred_at,
            }
        )

    def _refresh_runtime(self) -> None:
        current = monotonic()
        if self._runtime_last_reading is not None:
            self._elapsed_runtime += max(0.0, current - self._runtime_last_reading)
        self._runtime_last_reading = current

    def _append_error_event(
        self,
        stage: TaskStatus,
        *,
        step_number: int,
        attempt: int,
        cause_code: str,
    ) -> None:
        event = TaskEvent(
            task_id=self._task.task_id,
            event_type=TaskEventType.ERROR,
            to_status=self._task.status,
            message="stage execution failed",
            metadata={
                "stage": stage.value,
                "step_number": step_number,
                "attempt": attempt,
                "cause_code": cause_code,
            },
        )
        self._state = self._state.model_copy(
            update={
                "events": (*self._state.events, event),
                "updated_at": event.occurred_at,
            }
        )

    def _terminal_failure(self, code: str, message: str) -> OrchestrationResult:
        if self._state.status not in TERMINAL_STATUSES:
            self._task = self._task.model_copy(
                update={"error_code": code, "error_message": message}
            )
            self._transition(TaskStatus.FAILED, message=message, code=code)
        return self._result(error_code=code, error_message=message)

    def _cancelled_result(self) -> OrchestrationResult:
        code = "ORCHESTRATOR_CANCELLED"
        message = "orchestration cancelled"
        if self._state.status not in TERMINAL_STATUSES:
            self._task = self._task.model_copy(
                update={"error_code": code, "error_message": message}
            )
            self._transition(TaskStatus.CANCELLED, message=message, code=code)
        return self._result(error_code=code, error_message=message)

    def _result(
        self, *, error_code: str | None = None, error_message: str | None = None
    ) -> OrchestrationResult:
        checkpoint = self.checkpoint()
        return OrchestrationResult(
            task_id=checkpoint.task.task_id,
            status=self._state.status,
            state=self._state,
            output=dict(self._output),
            error_code=error_code,
            error_message=error_message,
            checkpoint=checkpoint,
        )
