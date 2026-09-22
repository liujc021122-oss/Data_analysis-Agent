from collections.abc import Callable, Mapping
from typing import Any

from ..domain.enums import TaskStatus
from ..domain.models import AgentState, AnalysisTask
from ..domain.state import transition_task
from .orchestration_errors import ToolUnavailableError
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
        tool_context_factory: Callable[[], Any] | None = None,
        initial_state: AgentState | None = None,
    ) -> None:
        state = initial_state or AgentState(task_id=task.task_id, status=task.status)
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
        self._tool_context_factory = tool_context_factory
        self._step_number = 0
        self._stage_attempts: dict[str, int] = {}
        self._context = dict(state.context)
        self._output: dict[str, Any] = {}
        self._report_generated = False

    def run(self) -> OrchestrationResult:
        if self._task.status is TaskStatus.PENDING:
            self._transition(TaskStatus.QUEUED)
        if self._task.status is TaskStatus.QUEUED:
            self._transition(TaskStatus.RUNNING)

        while self._task.status in ACTIVE_STAGES:
            stage = self._task.status
            attempt = self._stage_attempts.get(stage.value, 0)
            stage_input = StageInput(
                task=self._task,
                state=self._state,
                stage=stage,
                step_number=self._step_number,
                attempt=attempt,
                allowed_tools=STAGE_ALLOWED_TOOLS[stage],
                remaining_model_calls=self._limits.max_model_calls,
                context=dict(self._context),
            )
            result = self._handlers[stage](stage_input, self.call_tool)
            self._stage_attempts[stage.value] = attempt + 1
            self._step_number += 1
            self._context.update(result.context_updates)
            self._state = self._state.model_copy(update={"context": dict(self._context)})
            if result.output is not None:
                self._output[stage.value] = result.output
            if not result.completed:
                break
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
        raise NotImplementedError("cancellation is implemented in Task 3")

    def checkpoint(self) -> AgentCheckpoint:
        return AgentCheckpoint(
            task=self._task,
            state=self._state,
            step_number=self._step_number,
            stage_attempts=dict(self._stage_attempts),
            report_generated=self._report_generated,
            context=dict(self._context),
            output=dict(self._output),
        )

    def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        raise ToolUnavailableError("tool execution is implemented in Task 4")

    def _transition(self, target: TaskStatus) -> None:
        updated_task, event = transition_task(self._task, target)
        self._task = updated_task
        self._state = self._state.model_copy(
            update={
                "status": target,
                "events": (*self._state.events, event),
                "updated_at": event.occurred_at,
            }
        )
