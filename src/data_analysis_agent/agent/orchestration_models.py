from collections.abc import Mapping
import json
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

    @model_validator(mode="after")
    def validate_stage_attempts(self) -> "AgentCheckpoint":
        if any(attempt < 0 for attempt in self.stage_attempts.values()):
            raise ValueError("stage attempts must be non-negative")
        try:
            json.dumps(self.context, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint context must be JSON-safe") from exc
        return self


class OrchestrationResult(OrchestrationModel):
    task_id: UUID
    status: TaskStatus
    state: AgentState
    output: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    checkpoint: AgentCheckpoint

    @model_validator(mode="after")
    def validate_terminal_error(self) -> "OrchestrationResult":
        terminal_failure = self.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
        has_error = self.error_code is not None or self.error_message is not None
        if terminal_failure and (self.error_code is None or self.error_message is None):
            raise ValueError("failed or cancelled result requires an error code and message")
        if self.status is TaskStatus.COMPLETED and has_error:
            raise ValueError("completed result cannot carry an error")
        return self


class StageToolCaller(Protocol):
    def __call__(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        ...


class StageHandler(Protocol):
    def __call__(self, stage_input: StageInput, call_tool: StageToolCaller) -> StageResult:
        ...
