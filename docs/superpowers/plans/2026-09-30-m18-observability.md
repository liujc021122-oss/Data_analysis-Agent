# M18 Observability and Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add correlated JSON logging, durable runtime observations, model/token/cost accounting, sandbox resource snapshots, and owner-scoped trace/metrics APIs for every analysis task.

**Architecture:** Keep AuditEvent for user-operation auditing and add a dedicated observability port backed by three indexed tables: observability_events, llm_call_records, and sandbox_execution_records. A contextvars correlation context carries request_id, task_id, user_id, stage, tool, and model through API, Worker, LLM, and sandbox boundaries; a best-effort database writer and JSON logger consume the same sanitized observation models.

**Tech Stack:** Python 3.10+, Pydantic 2, standard-library logging and contextvars, SQLAlchemy 2, Alembic, FastAPI, pytest, SQLite test database, and the existing OpenAI-compatible LLM gateway.

## Global Constraints

- Do not add a runtime dependency for logging, tracing, metrics, or error reporting.
- Preserve the existing CallRecorder.record(metrics) single-argument protocol and all existing public LLM response shapes.
- Keep LLMCallMetrics.request_id as the provider request ID; map it to persisted provider_request_id and never overwrite the system correlation request_id.
- Use milliseconds for every duration field: duration in JSON logs and duration_ms in persistence/API models.
- Persist token usage as {input_tokens, output_tokens, total_tokens, estimated}; never persist prompt text or complete model responses.
- Persist only sanitized error messages and stacks; cap persisted error stacks at 8 KB and remove secrets, credentials, host paths, and raw user input.
- Unknown model prices produce estimated_cost_usd = null and increment unpriced_call_count; they never make a task or request fail.
- Observation writes are best effort. A failed write emits a safe diagnostic event and cannot alter the API, Worker, LLM, or sandbox business result.
- Ordinary users query only observations for tasks they own. Admin cross-user reads reuse the existing ADMIN_CROSS_USER_ACCESS audit path.
- Do not expose the internal task request_id in existing task DTOs; expose it only in the explicit observability trace response.
- Every task follows TDD: write the named failing test, run the exact focused command, implement the smallest passing change, rerun it, then commit.

---

### Task 1: Observation Core, Correlation Context, JSON Logging, and Price Configuration

**Files:**

- Create: src/data_analysis_agent/observability/__init__.py
- Create: src/data_analysis_agent/observability/models.py
- Create: src/data_analysis_agent/observability/context.py
- Create: src/data_analysis_agent/observability/redaction.py
- Create: src/data_analysis_agent/observability/logging.py
- Create: src/data_analysis_agent/observability/ports.py
- Create: tests/observability/test_models.py
- Create: tests/observability/test_context.py
- Create: tests/observability/test_logging.py
- Create: tests/observability/test_redaction.py
- Modify: src/data_analysis_agent/config/llm.py
- Modify: src/data_analysis_agent/config/settings.py
- Modify: tests/config/test_settings_contract.py

**Interfaces:**

- Consumes: existing services.errors.sanitize_text and sanitize_exception, existing LLMCallMetrics conventions, and Settings/LLMConfig construction.
- Produces: ObservationContext, get_observation_context(), bind_observation_context(), set_observation_context(), TokenUsage, ObservationEvent, LLMCallObservation, SandboxExecutionObservation, ObservabilityWriter, BestEffortObservabilityWriter, JsonLogFormatter, and parse_model_prices() for later tasks.

- [ ] Step 1: Write the failing tests for validated data, nested context restoration, redaction, logging, and price parsing.

~~~python
# tests/observability/test_context.py
from uuid import uuid4

from data_analysis_agent.observability.context import (
    bind_observation_context,
    get_observation_context,
)


def test_nested_context_restores_outer_values_after_exception():
    task_id = uuid4()
    assert get_observation_context().request_id is None
    try:
        with bind_observation_context(request_id="r-1", task_id=task_id):
            assert get_observation_context().request_id == "r-1"
            with bind_observation_context(stage="ANALYZING"):
                assert get_observation_context().stage == "ANALYZING"
            assert get_observation_context().stage is None
            raise RuntimeError("expected")
    except RuntimeError:
        pass
    assert get_observation_context().request_id is None
    assert get_observation_context().task_id is None


# tests/observability/test_models.py
import pytest
from pydantic import ValidationError

from data_analysis_agent.observability.models import ObservationEvent, TokenUsage


def test_observation_models_reject_negative_values_and_non_json_metadata():
    with pytest.raises(ValidationError):
        ObservationEvent(component="worker", event_type="stage_finished", duration_ms=-1)
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)
    with pytest.raises(ValidationError):
        ObservationEvent(component="worker", event_type="debug", metadata={"bad": object()})


# tests/config/test_settings_contract.py
def test_model_prices_parse_scalar_and_input_output_entries(tmp_path):
    settings = load_settings(
        app_env="test",
        dotenv_dir=tmp_path,
        environ={
            "LLM_MODEL_PRICES_JSON":
            '{"deepseek-chat": {"input": 0.14, "output": 0.28}, "cheap": 0.5}'
        },
    )
    assert settings.llm_config().model_prices["deepseek-chat"]["input"] == 0.14
    assert settings.llm_config().model_prices["cheap"] == 0.5


def test_invalid_model_price_is_rejected(tmp_path):
    with pytest.raises(ConfigurationError, match="LLM_MODEL_PRICES_JSON"):
        load_settings(
            app_env="test",
            dotenv_dir=tmp_path,
            environ={"LLM_MODEL_PRICES_JSON": '{"deepseek-chat": -1}'},
        )
~~~

Also assert that JsonLogFormatter output contains the stable keys timestamp, level, channel, component, event_type, request_id, task_id, user_id, stage, tool_name, model_name, duration, token_usage, and error_type, and that a secret/path never appears in the output.

- [ ] Step 2: Run the focused tests to verify the core package is absent.

Run: pytest tests/observability/test_context.py tests/observability/test_models.py tests/observability/test_logging.py tests/observability/test_redaction.py tests/config/test_settings_contract.py -k "nested_context or observation_models or json or redaction or model_prices or invalid_model_price" -v

Expected: FAIL during collection with ModuleNotFoundError: No module named data_analysis_agent.observability.

- [ ] Step 3: Implement the smallest core layer.

Create strict Pydantic models with these exact public fields:

~~~python
class TokenUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated: bool = False


class ObservationEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    request_id: str | None = Field(default=None, min_length=1)
    task_id: UUID | None = None
    user_id: UUID | None = None
    component: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=128)
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    channel: Literal["business", "debug"] = "business"
    stage: str | None = Field(default=None, max_length=64)
    tool_name: str | None = Field(default=None, max_length=128)
    model_name: str | None = Field(default=None, max_length=128)
    duration_ms: float | None = Field(default=None, ge=0)
    success: bool | None = None
    token_usage: TokenUsage | None = None
    error_type: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2000)
    error_stack: str | None = Field(default=None, max_length=8192)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LLMCallObservation(BaseModel):
    call_id: UUID
    request_id: str | None = None
    task_id: UUID | None = None
    user_id: UUID | None = None
    stage: str | None = None
    tool_name: str | None = None
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    provider_request_id: str | None = None
    attempt_count: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    token_usage: TokenUsage | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    success: bool
    error_type: str | None = None
    error_message: str | None = None
    error_stack: str | None = None


class SandboxExecutionObservation(BaseModel):
    execution_id: UUID
    request_id: str | None = None
    task_id: UUID
    user_id: UUID | None = None
    stage: str | None = None
    tool_name: str | None = None
    backend: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    success: bool
    exit_code: int | None = None
    timed_out: bool = False
    resource_limited: bool = False
    limits: dict[str, JSONValue] = Field(default_factory=dict)
    resource_usage: dict[str, JSONValue | None] = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    error_stack: str | None = None
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
~~~

Define `JSONValue` locally as a recursive JSON-safe type alias (`None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]`) and validate metadata with `json.dumps(metadata, allow_nan=False)` before persistence.

Implement context.py with an immutable ObservationContext dataclass in a ContextVar, bind_observation_context(**updates) as a context manager that always resets its token in finally, and set_observation_context(**updates) returning the reset token. redaction.py must call the existing sanitizer, use traceback.format_exception for stacks, remove credentials/host paths, and slice stacks to 8192 characters before model construction.

Implement ports.py as:

~~~python
class ObservabilityWriter(Protocol):
    def record_event(self, event: ObservationEvent) -> None: ...
    def record_llm_call(self, call: LLMCallObservation) -> None: ...
    def record_sandbox_execution(self, execution: SandboxExecutionObservation) -> None: ...


class BestEffortObservabilityWriter:
    def __init__(self, delegate: ObservabilityWriter, logger: logging.Logger): ...
    def record_event(self, event: ObservationEvent) -> None: ...
    def record_llm_call(self, call: LLMCallObservation) -> None: ...
    def record_sandbox_execution(self, execution: SandboxExecutionObservation) -> None: ...
~~~

Each wrapper catches Exception, logs event_type OBSERVABILITY_WRITE_FAILED with sanitized details, and returns None. JsonLogFormatter maps duration_ms to JSON duration, serializes UUID/datetime values, and adds nulls for unset stable fields. The logging configuration must preserve business/debug channels as record extras.

Update LLMConfig.model_prices to Mapping[str, float | Mapping[str, float]]. Add Settings.model_prices, parse LLM_MODEL_PRICES_JSON with json.loads, accept either a finite non-negative number or an object containing only finite non-negative input/output numbers, and pass settings.model_prices from Settings.llm_config(). Do not include secret values in Settings.to_dict().

- [ ] Step 4: Run the focused tests.

Run: pytest tests/observability/test_context.py tests/observability/test_models.py tests/observability/test_logging.py tests/observability/test_redaction.py tests/config/test_settings_contract.py -k "nested_context or observation_models or json or redaction or model_prices or invalid_model_price" -v

Expected: PASS for all selected tests.

- [ ] Step 5: Commit the core layer.

~~~bash
git add src/data_analysis_agent/observability src/data_analysis_agent/config/llm.py src/data_analysis_agent/config/settings.py tests/observability tests/config/test_settings_contract.py
git commit -m "feat: add observability context and structured logging"
~~~

### Task 2: Observation Persistence, Request Root IDs, and Alembic Migration

**Files:**

- Create: src/data_analysis_agent/persistence/observability_mappers.py
- Create: src/data_analysis_agent/persistence/observability_writer.py
- Create: alembic/versions/20260930_0007_observability.py
- Create: tests/database/test_observability_persistence.py
- Modify: src/data_analysis_agent/persistence/models.py
- Modify: src/data_analysis_agent/persistence/orm_models.py
- Modify: src/data_analysis_agent/persistence/repositories.py
- Modify: src/data_analysis_agent/persistence/unit_of_work.py
- Modify: src/data_analysis_agent/persistence/orm_mappers.py
- Modify: src/data_analysis_agent/persistence/__init__.py
- Modify: src/data_analysis_agent/services/persistence.py

**Interfaces:**

- Consumes: Task 1 observation models and ObservabilityWriter, Database.session_factory, existing UnitOfWork, AnalysisTaskRecord, and TaskPersistenceService.create_task_with_result_for_subject(subject=subject, request=request, request_id=request_id).
- Produces: ObservabilityEventRepository, LLMCallRepository, SandboxExecutionRepository, DatabaseObservabilityWriter, UnitOfWork.observability_events, UnitOfWork.llm_calls, UnitOfWork.sandbox_executions, and AnalysisTaskRecord.request_id persistence for Tasks 3-6.

- [ ] Step 1: Write failing persistence and migration tests.

~~~python
def test_writer_round_trips_trace_records(database):
    task_id = uuid4()
    event = ObservationEvent(
        request_id="request-1", task_id=task_id, component="worker",
        event_type="stage_finished", stage="ANALYZING", duration_ms=12.5,
    )
    writer = DatabaseObservabilityWriter(lambda: UnitOfWork(database.session_factory))
    writer.record_event(event)
    with UnitOfWork(database.session_factory) as uow:
        assert uow.observability_events.list_for_task(task_id)[0].event_id == event.event_id


def test_sandbox_observation_preserves_null_resource_values(database):
    observation = SandboxExecutionObservation(
        execution_id=uuid4(), task_id=uuid4(), backend="fake",
        started_at=aware_start, finished_at=aware_finish, duration_ms=1,
        success=True, code_sha256="0" * 64,
        resource_usage={"cpu_percent": None, "memory_bytes": None},
    )
    DatabaseObservabilityWriter(lambda: UnitOfWork(database.session_factory)).record_sandbox_execution(observation)
    with UnitOfWork(database.session_factory) as uow:
        saved = uow.sandbox_executions.get(observation.execution_id)
        assert saved.resource_usage["cpu_percent"] is None


def test_new_task_stores_root_request_id(database, task_persistence, subject, payload):
    created = task_persistence.create_task_with_result_for_subject(
        subject=subject, request=payload, request_id="request-root"
    )
    with UnitOfWork(database.session_factory) as uow:
        assert uow.tasks.get_request_id(created.task.task_id) == "request-root"
~~~

Add a migration test that runs alembic upgrade head twice and asserts analysis_tasks.request_id and all three tables exist.

- [ ] Step 2: Run the focused tests.

Run: pytest tests/database/test_observability_persistence.py -v

Expected: FAIL with an import or attribute error for DatabaseObservabilityWriter or UnitOfWork.observability_events before table assertions run.

- [ ] Step 3: Add ORM models, mappers, repositories, writer, and request-ID persistence.

Add request_id: str | None to AnalysisTaskRecord, AnalysisTaskORM, task_orm_to_record, and task_record_to_orm. Add TaskRepository.get_request_id(task_id) and expose it through TaskPersistenceService.get_task_request_id(task_id); do not add the field to the public task DTO. Add these three ORM records:

~~~python
class ObservabilityEventORM(Base):
    __tablename__ = "observability_events"
    __table_args__ = (
        Index("ix_observability_events_task_occurred", "task_id", "occurred_at", "event_id"),
        Index("ix_observability_events_request_occurred", "request_id", "occurred_at"),
        Index("ix_observability_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_observability_events_component_type_occurred", "component", "event_type", "occurred_at"),
        Index("ix_observability_events_model_occurred", "model_name", "occurred_at"),
        Index("ix_observability_events_stage_occurred", "stage", "occurred_at"),
    )
    event_id = mapped_column(UUIDString(), primary_key=True)
    request_id = mapped_column(String(128))
    task_id = mapped_column(ForeignKey("analysis_tasks.task_id"))
    user_id = mapped_column(ForeignKey("users.user_id"))
    component = mapped_column(String(64), nullable=False)
    event_type = mapped_column(String(128), nullable=False)
    level = mapped_column(String(16), nullable=False)
    channel = mapped_column(String(16), nullable=False)
    stage = mapped_column(String(64))
    tool_name = mapped_column(String(128))
    model_name = mapped_column(String(128))
    duration_ms = mapped_column(Float())
    success = mapped_column(Boolean())
    token_usage_json = mapped_column(JSON())
    error_type = mapped_column(String(128))
    error_message = mapped_column(Text())
    error_stack = mapped_column(Text())
    metadata_json = mapped_column(JSON(), nullable=False, default=dict)
    occurred_at = mapped_column(UTCDateTime(), nullable=False)


class LLMCallRecordORM(Base):
    __tablename__ = "llm_call_records"
    __table_args__ = (
        Index("ix_llm_call_records_task_started", "task_id", "started_at"),
        Index("ix_llm_call_records_request_started", "request_id", "started_at"),
        Index("ix_llm_call_records_model_started", "model_name", "started_at"),
        CheckConstraint("duration_ms >= 0", name="ck_llm_call_records_duration_non_negative"),
        CheckConstraint("attempt_count >= 1", name="ck_llm_call_records_attempt_positive"),
    )
    call_id = mapped_column(UUIDString(), primary_key=True)
    request_id = mapped_column(String(128))
    task_id = mapped_column(ForeignKey("analysis_tasks.task_id"))
    user_id = mapped_column(ForeignKey("users.user_id"))
    stage = mapped_column(String(64))
    tool_name = mapped_column(String(128))
    provider_name = mapped_column(String(64), nullable=False)
    model_name = mapped_column(String(128), nullable=False)
    provider_request_id = mapped_column(String(255))
    attempt_count = mapped_column(Integer(), nullable=False)
    started_at = mapped_column(UTCDateTime(), nullable=False)
    finished_at = mapped_column(UTCDateTime(), nullable=False)
    duration_ms = mapped_column(Float(), nullable=False)
    input_tokens = mapped_column(BigInteger())
    output_tokens = mapped_column(BigInteger())
    total_tokens = mapped_column(BigInteger())
    usage_estimated = mapped_column(Boolean(), nullable=False, default=False)
    estimated_cost_usd = mapped_column(Float())
    success = mapped_column(Boolean(), nullable=False)
    error_type = mapped_column(String(128))
    error_message = mapped_column(Text())
    error_stack = mapped_column(Text())
~~~

Define SandboxExecutionRecordORM with execution_id, correlation fields, backend/timestamps, duration_ms, success flags, exit_code, limits_json, resource_usage_json, error fields, and code_sha256; use indexes ix_sandbox_execution_records_task_started and ix_sandbox_execution_records_request_started plus a non-negative duration check. Repository methods are append, get, and list_for_task, ordered by (started_at, primary_id).

Implement DatabaseObservabilityWriter as a per-record transaction adapter:

~~~python
class DatabaseObservabilityWriter:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]): ...
    def record_event(self, event: ObservationEvent) -> None:
        with self.uow_factory() as uow:
            uow.observability_events.append(event)
            uow.commit()
    def record_llm_call(self, call: LLMCallObservation) -> None: ...
    def record_sandbox_execution(self, execution: SandboxExecutionObservation) -> None: ...
~~~

Extend TaskPersistenceService.create_task_with_result_for_subject with request_id: str | None = None and write it only for a newly created task. Add get_task_request_id(task_id) beside get_task_owner_id.

- [ ] Step 4: Implement the 20260930_0007 migration and run persistence tests.

The migration has revision 20260930_0007, down_revision 20260929_0006, creates the request-ID column, all three tables, checks, foreign keys, and named indexes, and drops them in reverse order on downgrade.

Run: pytest tests/database/test_observability_persistence.py -v

Expected: PASS, including the second upgrade head call and null resource values.

- [ ] Step 5: Commit the persistence layer.

~~~bash
git add src/data_analysis_agent/persistence src/data_analysis_agent/services/persistence.py alembic/versions/20260930_0007_observability.py tests/database/test_observability_persistence.py
git commit -m "feat: persist correlated observability records"
~~~

### Task 3: Trace and Metrics Query Service

**Files:**

- Create: src/data_analysis_agent/observability/metrics.py
- Create: src/data_analysis_agent/services/observability.py
- Create: tests/observability/test_metrics.py
- Create: tests/services/test_observability_service.py
- Modify: src/data_analysis_agent/observability/__init__.py

**Interfaces:**

- Consumes: Task 1 models, Task 2 repositories, TaskStatus, AnalysisTask, AccessSubject, and existing persistence errors.
- Produces: ModelMetrics, ObservabilityMetrics, TaskObservabilityTrace, ObservabilityQueryService.get_task_trace(), and ObservabilityQueryService.get_metrics() for Task 5.

- [ ] Step 1: Write failing tests for the aggregation formulas and owner boundary.

~~~python
def test_aggregate_metrics_counts_cancelled_separately_and_excludes_unknown_cost():
    result = aggregate_metrics(
        task_rows=[
            {"status": "COMPLETED", "duration_ms": 100},
            {"status": "FAILED", "duration_ms": 300},
            {"status": "CANCELLED", "duration_ms": 50},
        ],
        llm_rows=[
            {"model_name": "known", "input_tokens": 10, "output_tokens": 5,
             "total_tokens": 15, "estimated_cost_usd": 0.2, "success": True},
            {"model_name": "unknown", "input_tokens": 4, "output_tokens": 2,
             "total_tokens": 6, "estimated_cost_usd": None, "success": True},
        ],
    )
    assert result.success_rate == 0.5
    assert result.failure_rate == 0.5
    assert result.cancellation_rate == 1 / 3
    assert result.average_task_duration_ms == 200
    assert result.unpriced_call_count == 1
    assert result.by_model["known"].estimated_cost_usd == 0.2


def test_trace_service_rejects_foreign_task_with_same_not_found_contract(service, foreign_subject, task_id):
    assert service.get_task_trace(task_id, foreign_subject) is None
~~~

- [ ] Step 2: Run the focused tests.

Run: pytest tests/observability/test_metrics.py tests/services/test_observability_service.py -v

Expected: FAIL during collection with ModuleNotFoundError: No module named data_analysis_agent.observability.metrics.

- [ ] Step 3: Implement deterministic metrics and trace models.

Create ModelMetrics with model_name, call_count, success_count, failure_count, input_tokens, output_tokens, total_tokens, estimated_cost_usd, and unpriced_call_count. Create ObservabilityMetrics with task_count, completed_count, failed_count, cancelled_count, success_rate, failure_rate, cancellation_rate, average_task_duration_ms, total token fields, model_call_count, estimated_cost_usd, unpriced_call_count, and by_model: dict[str, ModelMetrics]. Create TaskObservabilityTrace with task identity/owner/root request ID, current status, terminal duration, total model calls/tokens/cost, and ordered tuples of ObservationEvent, LLMCallObservation, and SandboxExecutionObservation.

Implement aggregate_metrics() with these exact semantics:

~~~python
terminal = [row for row in task_rows if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}]
completed = sum(row["status"] == "COMPLETED" for row in terminal)
failed = sum(row["status"] == "FAILED" for row in terminal)
cancelled = sum(row["status"] == "CANCELLED" for row in terminal)
success_rate = completed / (completed + failed) if completed + failed else 0.0
failure_rate = failed / (completed + failed) if completed + failed else 0.0
cancellation_rate = cancelled / len(terminal) if terminal else 0.0
average_task_duration_ms = (
    sum(row["duration_ms"] for row in terminal) / len(terminal) if terminal else 0.0
)
~~~

Aggregate costs only when estimated_cost_usd is not None; increment unpriced_call_count otherwise. Treat missing token values as zero only during aggregation and never write zero back into a persisted record.

- [ ] Step 4: Implement the owner-scoped query service.

Add:

~~~python
class ObservabilityQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]): ...
    def get_task_trace(self, task_id: UUID, subject: AccessSubject) -> TaskObservabilityTrace | None: ...
    def get_metrics(
        self,
        subject: AccessSubject,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        model_name: str | None = None,
        status: TaskStatus | None = None,
    ) -> ObservabilityMetrics: ...
~~~

For ordinary users, filter through uow.tasks.get_for_subject/list_for_subject; for admins use the existing AccessSubject admin path. Return None for unknown/foreign traces before reading observation tables. Sort trace lists by (occurred_at|started_at, id), filter metrics by UTC time range/model/status, and calculate terminal task duration from created_at to updated_at.

- [ ] Step 5: Run query tests.

Run: pytest tests/observability/test_metrics.py tests/services/test_observability_service.py -v

Expected: PASS, including success-rate denominator, separate cancellation rate, unknown-cost count, and foreign-task isolation.

- [ ] Step 6: Commit the query service.

~~~bash
git add src/data_analysis_agent/observability src/data_analysis_agent/services/observability.py tests/observability/test_metrics.py tests/services/test_observability_service.py
git commit -m "feat: add observability trace and metrics queries"
~~~

### Task 4: LLM, Worker, API Context, and Sandbox Integration

**Files:**

- Create: src/data_analysis_agent/observability/adapters.py
- Create: tests/observability/test_adapters.py
- Create or modify: tests/llm/test_observability_recorder.py
- Modify: src/data_analysis_agent/llm/models.py
- Modify: src/data_analysis_agent/llm/client.py
- Modify: src/data_analysis_agent/services/llm.py
- Modify: src/data_analysis_agent/agent/core.py
- Modify: src/data_analysis_agent/execution/models.py
- Modify: src/data_analysis_agent/execution/runtime.py
- Modify: src/data_analysis_agent/execution/agent_session.py
- Modify: src/data_analysis_agent/worker/worker.py
- Modify: src/data_analysis_agent/worker/cli.py
- Modify: src/data_analysis_agent/api/app.py
- Modify: src/data_analysis_agent/api/auth.py
- Modify: src/data_analysis_agent/services/persistence.py
- Modify: tests/worker/test_worker.py
- Modify: tests/execution/test_container_executor.py

**Interfaces:**

- Consumes: Task 1 context/writer/redaction types, Task 2 DatabaseObservabilityWriter, existing LLMClient, AnalysisTaskWorker, AgentExecutionSession, RuntimeWaitResult, and request middleware.
- Produces: LLMObservationRecorder, sandbox observation emission, stage/task events, resource usage fields, and API-to-Worker-to-LLM/sandbox correlation for Task 5.

- [ ] Step 1: Write failing integration tests for LLM failures, Worker stages, and sandbox resource snapshots.

~~~python
def test_llm_observation_recorder_maps_provider_request_id_and_failure_stack(writer):
    metrics = LLMCallMetrics(
        call_id=uuid4(), provider="deepseek", model="deepseek-chat",
        attempt_count=2, started_at=aware_start, finished_at=aware_finish,
        duration_ms=25, request_id="provider-call-7", success=False,
        error_type="LLM_TIMEOUT", error_message="timeout", error_stack="safe stack",
    )
    LLMObservationRecorder(writer).record(metrics)
    saved = writer.llm_calls[0]
    assert saved.request_id == "system-request"
    assert saved.provider_request_id == "provider-call-7"
    assert saved.success is False
    assert saved.error_stack == "safe stack"


def test_worker_records_stage_duration_and_error_stack(worker, writer):
    result = worker.process(task_id)
    assert result.status is TaskStatus.FAILED
    events = writer.events_for(task_id)
    assert {event.event_type for event in events} >= {
        "task_started", "stage_finished", "task_failed"
    }
    assert all(event.request_id == f"task:{task_id}" for event in events)
    assert any(event.error_stack and len(event.error_stack) <= 8192 for event in events)


def test_agent_session_persists_resource_usage_without_fabricating_missing_values(fake_backend, writer):
    fake_backend.wait_result = RuntimeWaitResult(
        exit_code=0,
        resource_usage={"cpu_percent": 12.5, "memory_bytes": 4096, "pids": 2},
    )
    session = make_session(fake_backend, writer=writer)
    session.execute_code("result = 1")
    assert writer.sandbox_calls[0].resource_usage["memory_bytes"] == 4096
~~~

- [ ] Step 2: Run focused integration tests.

Run: pytest tests/observability/test_adapters.py tests/llm/test_observability_recorder.py tests/worker/test_worker.py -k "observab or stage_duration or resource_usage" -v

Expected: FAIL with missing LLMObservationRecorder or missing LLMCallMetrics.success/RuntimeWaitResult.resource_usage.

- [ ] Step 3: Extend LLM metrics without breaking the recorder protocol.

Add these default fields to LLMCallMetrics:

~~~python
success: bool = True
error_type: str | None = None
error_message: str | None = None
error_stack: str | None = None
~~~

In every terminal success/failure path in LLMClient.achat() and LLMClient.astream(), create metrics once and set success=False, error_type to the stable LLM error code or mapped exception type, sanitized error_message, and a sanitized/truncated stack on failure. Keep _record(metrics) as the existing one-argument call. Add LLMObservationRecorder.record(metrics) that reads get_observation_context(), maps metrics.request_id to provider_request_id, maps metrics.usage to TokenUsage, and writes LLMCallObservation through the best-effort writer.

Update LLMHelper.__init__(config=None, gateway=None, recorder=None) to pass recorder only when constructing LLMClient. An injected gateway remains authoritative. Update DataAnalysisAgent with optional observability_writer, construct LLMObservationRecorder once, and pass it to LLMHelper without changing injected llm behavior.

- [ ] Step 4: Add Worker context and lifecycle observations.

Extend TaskPersistenceService with get_task_request_id(task_id). In AnalysisTaskWorker.process(), after claim load owner and root request ID, choose request_id or f"task:{task_id}", and bind:

~~~python
with bind_observation_context(
    request_id=request_id, task_id=task.task_id, user_id=owner_id
):
    writer.record_event(ObservationEvent(
        component="worker", event_type="task_started", ...
    ))
    # existing claim/run/retry/complete logic
    writer.record_event(ObservationEvent(
        component="worker", event_type="task_finished",
        duration_ms=elapsed_ms, success=terminal_success,
    ))
~~~

Use try/finally so finish and context reset happen on success, failure, retry, and cancellation. Do not change retry or cancellation behavior. In _on_agent_transition(), emit stage_started and stage_finished with TaskStatus.value, duration from _phase_started_at, and only allowlisted metadata. On _handle_failure(), emit sanitized exception type/message/stack while preserving the existing WorkerResult.error_message cap.

Update worker/cli.py to construct one DatabaseObservabilityWriter from the worker database UnitOfWork factory, wrap it in BestEffortObservabilityWriter, pass the wrapper to build_worker(), and pass the same writer to every DataAnalysisAgent factory.

- [ ] Step 5: Bind API request/user context and root request IDs.

In request_id_middleware, bind the generated/validated ID for the whole request and reset it in finally; preserve X-Request-ID on handled errors. In get_current_principal, call set_observation_context(user_id=principal.user_id) after successful authentication. Pass the existing request.state.request_id through task creation into analysis_tasks.request_id. Add API start/end/error business events with no body, password, prompt, response, or raw input metadata.

- [ ] Step 6: Add sandbox resource snapshots and observation emission.

Extend RuntimeWaitResult with resource_usage: Mapping[str, int | float | None] | None = None. Extend ExecutionResult and ExecutionAudit with resource_usage: dict[str, int | float | None] = Field(default_factory=dict). DockerCliRuntime.wait() returns an allowlisted snapshot with cpu_percent, memory_bytes, pids, and output_bytes; unavailable values are None, never fabricated zeroes. Local/fake runtimes return empty or null values. AgentExecutionSession._record_audit() copies limits/resource usage and calls SandboxObservationRecorder.record(audit, limits=self.limits.model_dump(mode="json")) after appending the audit. The recorder uses current context, audit ID/code hash/backend/times/status, and sanitized errors.

- [ ] Step 7: Run integration and regression tests.

Run: pytest tests/observability/test_adapters.py tests/llm/test_observability_recorder.py tests/llm/test_client_chat.py tests/worker/test_worker.py tests/execution -v

Expected: PASS, including existing LLM recorder/usage/redaction tests and distinct system/provider request IDs.

- [ ] Step 8: Commit the runtime integration.

~~~bash
git add src/data_analysis_agent/observability src/data_analysis_agent/llm src/data_analysis_agent/services/llm.py src/data_analysis_agent/agent/core.py src/data_analysis_agent/execution src/data_analysis_agent/worker src/data_analysis_agent/api/app.py src/data_analysis_agent/api/auth.py src/data_analysis_agent/services/persistence.py tests/observability tests/llm tests/worker tests/execution
git commit -m "feat: correlate worker llm and sandbox observations"
~~~

### Task 5: Observability Query API and Authorization

**Files:**

- Create: src/data_analysis_agent/api/routers/observability.py
- Create: tests/api/test_observability.py
- Modify: src/data_analysis_agent/api/application.py
- Modify: src/data_analysis_agent/api/app.py
- Modify: src/data_analysis_agent/api/routers/__init__.py
- Modify: src/data_analysis_agent/api/schemas.py
- Modify: tests/api/test_app.py
- Modify: tests/api/test_authorization.py only for shared authorization assertions if needed

**Interfaces:**

- Consumes: Task 3 ObservabilityQueryService, Task 1 observation/metrics models, existing Principal, AccessSubject, APIError, record_cross_user_access, and APIApplication dependency assembly.
- Produces: GET /api/observability/tasks/{task_id} and GET /api/observability/metrics with stable response schemas and existing not-found/admin-audit semantics.

- [ ] Step 1: Write failing route, schema, and authorization tests.

~~~python
def test_task_trace_contains_api_worker_llm_and_sandbox_records(observability_api):
    client, task_id = observability_api.seed_trace()
    response = client.get(f"/api/observability/tasks/{task_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == str(task_id)
    assert body["events"][0]["request_id"] == "request-root"
    assert len(body["llm_calls"]) == 1
    assert len(body["sandbox_executions"]) == 1


def test_foreign_trace_is_hidden_and_admin_cross_user_read_is_audited(observability_api):
    _, task_id = observability_api.seed_trace(owner="owner@example.com")
    other_client = observability_api.login("other@example.com")
    assert other_client.get(f"/api/observability/tasks/{task_id}").status_code == 404
    admin = observability_api.login("admin@example.com")
    assert admin.get(f"/api/observability/tasks/{task_id}").status_code == 200
    assert observability_api.audit_actions()[-1] == "ADMIN_CROSS_USER_ACCESS"


def test_metrics_expose_success_failure_average_cost_and_unpriced_count(observability_api):
    client = observability_api.seed_metrics()
    body = client.get("/api/observability/metrics?model_name=deepseek-chat").json()
    assert body["success_rate"] == 1.0
    assert body["average_task_duration_ms"] == 100.0
    assert body["by_model"]["deepseek-chat"]["call_count"] == 2
~~~

Add an OpenAPI assertion for both paths to tests/api/test_app.py.

- [ ] Step 2: Run the focused API tests.

Run: pytest tests/api/test_observability.py tests/api/test_app.py -k "observab or openapi" -v

Expected: FAIL with 404 for the new routes or an import error for the router/response models.

- [ ] Step 3: Add response DTOs without prompt/response/path leakage.

Add ObservationEventResponse, LLMCallObservationResponse, SandboxExecutionResponse, TaskObservabilityResponse, ModelMetricsResponse, and ObservabilityMetricsResponse in api/schemas.py. Expose sanitized error fields, token/cost/duration/resource fields, and the internal root request_id only in TaskObservabilityResponse. Do not add query, prompt, raw model text, source code, host paths, or secrets to any observation DTO. Keep extra="forbid".

- [ ] Step 4: Implement the router and application wiring.

Register routes with these exact signatures:

~~~python
@router.get("/tasks/{task_id}", response_model=TaskObservabilityResponse)
def get_task_trace(
    request: Request,
    task_id: UUID,
    principal: Principal = Depends(get_current_principal),
):
    subject = _subject(principal)
    trace = request.app.state.api_application.observability_query.get_task_trace(task_id, subject)
    if trace is None:
        raise _not_found()
    return _task_observability_response(trace)


@router.get("/metrics", response_model=ObservabilityMetricsResponse)
def get_metrics(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    model_name: str | None = Query(default=None, min_length=1, max_length=128),
    status: TaskStatus | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
):
    metrics = request.app.state.api_application.observability_query.get_metrics(
        _subject(principal), since=since, until=until, model_name=model_name, status=status
    )
    return _metrics_response(metrics)
~~~

Add observability_writer and observability_query fields to APIApplication. In from_settings(), construct DatabaseObservabilityWriter wrapped by BestEffortObservabilityWriter and ObservabilityQueryService from the same UnitOfWork factory. The trace route calls the service with _subject(principal), returns the existing uniform not-found response for missing/foreign tasks, and calls record_cross_user_access after an admin reads a foreign task. Metrics use the same subject filter.

- [ ] Step 5: Run API and authorization tests.

Run: pytest tests/api/test_observability.py tests/api/test_app.py tests/api/test_authorization.py -v

Expected: PASS; ordinary users receive the same 404 body for foreign and unknown task IDs, admins receive the trace, and admin reads create ADMIN_CROSS_USER_ACCESS.

- [ ] Step 6: Commit the API layer.

~~~bash
git add src/data_analysis_agent/api tests/api/test_observability.py tests/api/test_app.py tests/api/test_authorization.py
git commit -m "feat: expose owner-scoped observability APIs"
~~~

### Task 6: Public Exports, Documentation, Configuration Examples, and End-to-End Verification

**Files:**

- Modify: src/data_analysis_agent/__init__.py
- Modify: src/data_analysis_agent/observability/__init__.py
- Modify: src/data_analysis_agent/persistence/__init__.py
- Modify: README.md
- Modify: .env.example
- Modify: .env.development.example
- Modify: .env.test.example
- Modify: .env.production.example
- Create: tests/integration/test_m18_observability_flow.py
- Modify: tests/api/test_app.py

**Interfaces:**

- Consumes: all previous tasks and existing package export/documentation conventions.
- Produces: importable public observation types, documented LLM_MODEL_PRICES_JSON, one end-to-end correlation test, and release-quality verification evidence.

- [ ] Step 1: Write the failing end-to-end and export/import tests.

~~~python
def test_task_id_reconstructs_api_worker_llm_and_sandbox_chain(e2e_app):
    client = e2e_app.client_for_user()
    created = client.post(
        "/api/tasks",
        json={"query": "summarize", "idempotency_key": "m18-e2e"},
        headers={"X-Request-ID": "request-e2e"},
    )
    assert created.status_code == 202
    task_id = created.json()["task_id"]
    e2e_app.run_worker_once(task_id)
    trace = client.get(f"/api/observability/tasks/{task_id}")
    assert trace.status_code == 200
    body = trace.json()
    assert {event["component"] for event in body["events"]} >= {"api", "worker"}
    assert body["llm_calls"][0]["request_id"] == "request-e2e"
    assert body["sandbox_executions"][0]["task_id"] == task_id


def test_observability_types_are_publicly_importable():
    from data_analysis_agent import ObservationEvent, ObservabilityMetrics
    assert ObservationEvent is not None
    assert ObservabilityMetrics is not None
~~~

- [ ] Step 2: Run the focused end-to-end test.

Run: pytest tests/integration/test_m18_observability_flow.py -v

Expected: FAIL because final exports and/or API/Worker/LLM/sandbox writer wiring do not exist.

- [ ] Step 3: Complete exports and documentation.

Export Task 1 observation models, context helpers, writer protocol, and Task 3 metrics models from observability/__init__.py and the package root without exporting private repository internals. Export persistence repository/ORM classes from persistence/__init__.py following existing conventions.

Document this exact configuration in README.md and the existing environment example:

~~~dotenv
# Model price in USD per 1M tokens. Scalar applies to both input/output.
LLM_MODEL_PRICES_JSON={"deepseek-chat":{"input":0.14,"output":0.28}}
~~~

Document both endpoints, the success-rate denominator COMPLETED / (COMPLETED + FAILED), separate cancellation treatment, unpriced_call_count, fallback request ID task:<task_id>, and the privacy rule excluding prompts/responses/source code/secrets/host paths.

- [ ] Step 4: Run end-to-end, full, compile, install, migration, and whitespace verification.

Run: pytest tests/integration/test_m18_observability_flow.py tests/observability tests/database/test_observability_persistence.py tests/api/test_observability.py -v

Expected: PASS with one trace containing API, Worker, LLM, and sandbox records linked by the same request/task/user context.

Run: pytest -q

Expected: PASS with no regression in M01-M17 tests.

Run: python -m compileall -q src

Expected: exit code 0.

Run: python -m pip install -e .

Expected: editable install succeeds and the package imports from the workspace.

Run: alembic upgrade head; alembic upgrade head

Expected: both commands exit 0; the second reports no pending migration work.

Run: git diff --check

Expected: no output and exit code 0.

- [ ] Step 5: Commit documentation and release verification changes.

~~~bash
git add src/data_analysis_agent/__init__.py src/data_analysis_agent/observability/__init__.py src/data_analysis_agent/persistence/__init__.py README.md tests/integration/test_m18_observability_flow.py tests/api/test_app.py
git commit -m "docs: document M18 observability and cost metrics"
~~~

## Self-Review Checklist

- Spec coverage: JSON logs and business/debug channels are Task 1; durable event/LLM/sandbox detail and request roots are Task 2; success/failure/average duration/model cost formulas are Task 3; API/Worker/LLM/sandbox correlation, error stacks, and resource snapshots are Task 4; query endpoints and owner/admin authorization are Task 5; future exporter boundaries remain the ObservabilityWriter port and are documented in Task 6.
- Privacy coverage: redaction and the 8 KB stack cap are Task 1 and enforced again by adapters; API DTOs exclude prompts, responses, source, secrets, and host paths in Task 5.
- Compatibility coverage: CallRecorder.record(metrics) remains unchanged, provider IDs remain separate, task DTOs remain unchanged, and observation failures are wrapped best effort.
- Migration coverage: Task 2 defines revision 20260930_0007 after 20260929_0006, named indexes/checks, downgrade order, and repeated-upgrade verification.
- Type consistency: later tasks consume exactly ObservationEvent, LLMCallObservation, SandboxExecutionObservation, ObservabilityWriter, ObservabilityQueryService.get_task_trace(), ObservabilityQueryService.get_metrics(), TaskObservabilityTrace, and ObservabilityMetrics defined in earlier tasks.

Plan complete and saved to docs/superpowers/plans/2026-09-30-m18-observability.md. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
