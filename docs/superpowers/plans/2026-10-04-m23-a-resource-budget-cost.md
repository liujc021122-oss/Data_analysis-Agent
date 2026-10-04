# M23-A Resource Budget and Cost Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-backed user task quotas, task-scoped model/chart budgets, and queryable LLM usage/cost accounting without changing existing task-message, provider, or compatibility contracts.

**Architecture:** A validated immutable `ResourceLimits` object supplies finite limits to the API, Worker, Agent, and execution boundary. The database remains authoritative for active-task admission and cumulative task usage; a task-local `TaskBudget` performs fast model/chart checks during execution. `LLMClient` keeps its existing recorder contract and gains an optional pre-call hook so retries and structured-output correction requests are counted without exposing prompts or responses.

**Tech Stack:** Python 3.10+, Pydantic 2, SQLAlchemy 2, Alembic, FastAPI, pytest, existing `LLMClient`/`AgentLLMPort`/`TaskSubmissionService`/`AnalysisTaskWorker`; no new runtime dependency and no Redis quota counter.

## Global Constraints

- `max_active_tasks_per_user` defaults to `3`.
- `max_model_calls_per_task` defaults to `20` and must include structured-output correction requests.
- `max_chart_count` defaults to `20`.
- `max_chart_file_bytes` defaults to `10 * 1024 * 1024`.
- `max_chart_total_bytes` defaults to `50 * 1024 * 1024`.
- `max_model_cost_usd_per_task` is disabled when unset, but measured cost is always recorded.
- Existing `ORCHESTRATOR_MAX_MODEL_CALLS`, `ExecutionLimits.max_output_bytes`, `ExecutionLimits.max_files`, Worker retry rules, and public compatibility constructors remain valid.
- The task message sent to a broker contains only `task_id`.
- Database records contain counters and safe error metadata only; never store prompts, model responses, credentials, URLs, or host paths.
- Every production-code change has a failing test first; each task ends with focused pytest, `git diff --check`, and an explicit commit containing only its files.
- Tests use `E:\anaconda\python.exe -m pytest`; no live LLM, Redis, Celery, Docker, or network is required.

## File Map

- Create `src/data_analysis_agent/config/resource.py` for validated immutable resource-limit values.
- Create `src/data_analysis_agent/services/resource_budget.py` for quota errors, task-local budget checks, and the task-bound LLM usage recorder.
- Modify `src/data_analysis_agent/config/settings.py` to parse resource environment variables and expose `resource_limits()`.
- Modify `src/data_analysis_agent/domain/models.py`, `src/data_analysis_agent/persistence/models.py`, `src/data_analysis_agent/persistence/orm_models.py`, `src/data_analysis_agent/persistence/orm_mappers.py`, and `src/data_analysis_agent/persistence/mappers.py` to carry task usage fields.
- Modify `src/data_analysis_agent/persistence/repositories.py` and `src/data_analysis_agent/services/persistence.py` for atomic usage updates, active-task counts, user locking, quota-aware creation, and quota-aware retry.
- Create `alembic/versions/20261004_0008_task_resource_usage.py` for the six usage columns and non-negative constraints.
- Modify `src/data_analysis_agent/worker/service.py`, `src/data_analysis_agent/worker/worker.py`, `src/data_analysis_agent/worker/cli.py`, and `src/data_analysis_agent/worker/errors.py` to inject limits/budgets and preserve non-retryable quota errors.
- Modify `src/data_analysis_agent/llm/client.py`, `src/data_analysis_agent/services/llm.py`, `src/data_analysis_agent/agent/core.py`, and `src/data_analysis_agent/agent/llm_port.py` to enforce pre-call budgets and record metrics.
- Modify `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/routers/tasks.py`, `src/data_analysis_agent/api/application.py`, and `src/data_analysis_agent/api/app.py` for usage output and HTTP 429 quota errors.
- Modify `README.md` and `docs/deployment/local-compose.md` with resource environment variables and task usage semantics.
- Add focused tests under `tests/config`, `tests/services`, `tests/database`, `tests/worker`, `tests/llm`, `tests/agent`, and `tests/api`.

---

### Task 1: Resource Limits and Task-Local Budget Contract

**Files:**
- Create: `src/data_analysis_agent/config/resource.py`
- Create: `src/data_analysis_agent/services/resource_budget.py`
- Modify: `src/data_analysis_agent/config/settings.py`
- Test: `tests/config/test_resource_settings.py`
- Test: `tests/services/test_resource_budget.py`

**Interfaces:**
- `ResourceLimits` is a frozen dataclass with the six limits from Global Constraints and a `validate()` method.
- `Settings.resource_limits() -> ResourceLimits` returns a new immutable value object.
- `ResourceQuotaExceededError` exposes `code`, `resource`, `limit`, `observed`, `retryable=False`, and `details()`.
- `TaskBudget.from_usage(limits, model_call_count, estimated_model_cost_usd, chart_count=0, chart_total_bytes=0) -> TaskBudget`.
- `TaskBudget.check_model_call()`, `TaskBudget.record_model_usage(metrics)`, and `TaskBudget.check_chart_output(file_bytes)` are the only budget operations used by later tasks.

- [ ] **Step 1: Write the failing tests.**

Add tests that specify the public contract before creating production modules:

```python
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

    assert settings.resource_limits().max_active_tasks_per_user == 4
    assert settings.resource_limits().max_model_calls_per_task == 7
    assert settings.resource_limits().max_chart_total_bytes == 4096


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


def test_resource_limits_reject_non_finite_or_non_positive_values():
    with pytest.raises(ValueError):
        ResourceLimits(max_active_tasks_per_user=0)
    with pytest.raises(ValueError):
        ResourceLimits(max_model_cost_usd_per_task=float("nan"))
```

- [ ] **Step 2: Run the focused tests and verify the intended RED result.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/config/test_resource_settings.py tests/services/test_resource_budget.py -q
```

Expected: collection fails because `ResourceLimits`, `TaskBudget`, and `Settings.resource_limits()` do not exist. If the tests fail for an import typo instead, correct the test imports before writing production code.

- [ ] **Step 3: Implement the minimal resource contract.**

Create `ResourceLimits` with frozen dataclass validation. Use the exact defaults `3`, `20`, `20`, `10 * 1024 * 1024`, and `50 * 1024 * 1024`; allow only `None` or a finite non-negative cost cap. Add `MAX_ACTIVE_TASKS_PER_USER`, `MAX_MODEL_CALLS_PER_TASK`, `MAX_MODEL_COST_USD_PER_TASK`, `MAX_CHART_COUNT`, `MAX_CHART_FILE_BYTES`, and `MAX_CHART_TOTAL_BYTES` parsing to `load_settings()` using the existing positive/non-negative parser helpers. Add `Settings.resource_limits()` without importing service code.

Implement `ResourceQuotaExceededError` with safe messages such as `model calls exceeded configured limit`; its `details()` must contain only `resource`, `limit`, `observed`, and `retryable`. Implement `TaskBudget` so `check_model_call()` increments a local reservation only after passing, `record_model_usage()` adds the finite metric cost and raises `TASK_MODEL_COST_LIMIT` when a configured cap is exceeded, and `check_chart_output()` checks single-file and cumulative limits before the caller stores the chart.

- [ ] **Step 4: Run the focused tests and verify GREEN.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/config/test_resource_settings.py tests/services/test_resource_budget.py -q
```

Expected: all new resource-limit tests pass. Also run `E:\anaconda\python.exe -m pytest tests/config -q` and confirm the existing settings contract remains green.

- [ ] **Step 5: Commit the contract layer.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/config/resource.py src/data_analysis_agent/config/settings.py src/data_analysis_agent/services/resource_budget.py tests/config/test_resource_settings.py tests/services/test_resource_budget.py
git commit -m "feat: add task resource budget policy"
```

Expected: one commit containing only the resource-limit contract and tests.

### Task 2: Persist Task Usage and Add the Database Migration

**Files:**
- Modify: `src/data_analysis_agent/domain/models.py`
- Modify: `src/data_analysis_agent/persistence/models.py`
- Modify: `src/data_analysis_agent/persistence/orm_models.py`
- Modify: `src/data_analysis_agent/persistence/orm_mappers.py`
- Modify: `src/data_analysis_agent/persistence/mappers.py`
- Modify: `src/data_analysis_agent/persistence/repositories.py`
- Modify: `src/data_analysis_agent/services/persistence.py`
- Create: `alembic/versions/20261004_0008_task_resource_usage.py`
- Test: `tests/domain/test_models.py`
- Test: `tests/database/test_sqlalchemy_mappers.py`
- Test: `tests/database/test_task_lifecycle.py`
- Create: `tests/database/test_task_resource_usage.py`

**Interfaces:**
- `AnalysisTask`, `AnalysisTaskRecord`, and `AnalysisTaskORM` expose `model_input_tokens`, `model_output_tokens`, `model_total_tokens`, and `estimated_model_cost_usd` with non-negative defaults.
- `TaskRepository.record_model_usage(task_id, *, duration_ms, input_tokens, output_tokens, total_tokens, estimated_cost_usd) -> AnalysisTask` atomically increments all six usage values, including the existing call count.
- `TaskPersistenceService.record_model_usage(...) -> AnalysisTask` owns the transaction and commit.
- Existing `record_model_call(task_id, duration_ms)` delegates to the new method with zero token/cost increments.

- [ ] **Step 1: Write the failing persistence and mapping tests.**

Add a domain round-trip and atomic accumulation test:

```python
def test_analysis_task_has_non_negative_usage_defaults():
    task = AnalysisTask(query="分析销售")

    assert task.model_input_tokens == 0
    assert task.model_output_tokens == 0
    assert task.model_total_tokens == 0
    assert task.estimated_model_cost_usd == 0.0


def test_record_model_usage_accumulates_metrics_without_prompt_data(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    task = persistence.create_task(
        user_id=uuid4(),
        request=AnalysisTaskCreateRequest(
            query="分析销售", idempotency_key="usage-key"
        ),
    )

    persistence.record_model_usage(
        task_id=task.task_id,
        duration_ms=120,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        estimated_cost_usd=0.0004,
    )
    second = persistence.record_model_usage(
        task_id=task.task_id,
        duration_ms=80,
        input_tokens=4,
        output_tokens=6,
        total_tokens=10,
        estimated_cost_usd=0.0002,
    )

    assert second.model_call_count == 2
    assert second.model_duration_ms == 200
    assert second.model_input_tokens == 14
    assert second.model_output_tokens == 11
    assert second.model_total_tokens == 25
    assert second.estimated_model_cost_usd == pytest.approx(0.0006)
    assert "prompt" not in second.metadata
```

Add mapper tests asserting ORM-to-domain and domain-to-record preserve all four new fields, and add a negative-value test for each field. Add a schema test asserting the migration creates non-negative constraints and defaults old rows to zero.

- [ ] **Step 2: Run the focused tests and verify RED.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/domain/test_models.py tests/database/test_sqlalchemy_mappers.py tests/database/test_task_resource_usage.py -q
```

Expected: collection or assertion failure because the new fields and `record_model_usage()` do not exist. Do not change the tests to match the current schema.

- [ ] **Step 3: Implement model, ORM, mapper, repository, and migration changes.**

Add the four non-negative fields to `AnalysisTask` and `AnalysisTaskRecord`. Add `BigInteger` columns and a `Numeric(20, 12)` cost column to `AnalysisTaskORM`, with server default `0` and check constraints for all four values. Update `task_to_record()`, `record_to_task()`, `analysis_task_record_to_orm()`, and `analysis_task_orm_to_record()`.

Implement the repository update using one SQL `UPDATE` statement:

```python
statement = (
    update(AnalysisTaskORM)
    .where(AnalysisTaskORM.task_id == task_id)
    .values(
        model_call_count=AnalysisTaskORM.model_call_count + 1,
        model_duration_ms=AnalysisTaskORM.model_duration_ms + duration_ms,
        model_input_tokens=AnalysisTaskORM.model_input_tokens + input_tokens,
        model_output_tokens=AnalysisTaskORM.model_output_tokens + output_tokens,
        model_total_tokens=AnalysisTaskORM.model_total_tokens + total_tokens,
        estimated_model_cost_usd=(
            AnalysisTaskORM.estimated_model_cost_usd + estimated_cost_usd
        ),
        updated_at=utc_now(),
    )
)
```

Validate every increment as finite and non-negative before executing it. Keep `record_model_call()` as a compatibility wrapper. Add `TaskPersistenceService.record_model_usage()` with the existing Unit of Work commit pattern.

Create revision `20261004_0008_task_resource_usage` with `down_revision = "20261002_0007"`; add the four columns with non-null zero defaults and matching check constraints, and remove them in `downgrade()` in reverse order.

- [ ] **Step 4: Run focused database tests and migration checks.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/domain/test_models.py tests/database/test_sqlalchemy_mappers.py tests/database/test_task_resource_usage.py tests/database/test_task_lifecycle.py -q
E:\anaconda\python.exe -m pytest tests/database/test_schema.py -q
E:\anaconda\python.exe -m alembic upgrade head
```

Expected: all focused tests pass, the schema test sees the four new non-negative fields, and Alembic exits with code 0. If the local database is not configured, run the migration through the existing SQLite fixture command used by `tests/database/conftest.py` and record that environment limitation in `progress.md`.

- [ ] **Step 5: Commit the persistence slice.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/domain/models.py src/data_analysis_agent/persistence/models.py src/data_analysis_agent/persistence/orm_models.py src/data_analysis_agent/persistence/orm_mappers.py src/data_analysis_agent/persistence/mappers.py src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/services/persistence.py alembic/versions/20261004_0008_task_resource_usage.py tests/domain/test_models.py tests/database/test_sqlalchemy_mappers.py tests/database/test_task_lifecycle.py tests/database/test_task_resource_usage.py
git commit -m "feat: persist task model usage and cost"
```

### Task 3: Enforce User Activity Quotas at Submission and Retry

**Files:**
- Modify: `src/data_analysis_agent/persistence/repositories.py`
- Modify: `src/data_analysis_agent/services/persistence.py`
- Modify: `src/data_analysis_agent/worker/service.py`
- Modify: `src/data_analysis_agent/api/routers/tasks.py`
- Modify: `src/data_analysis_agent/api/application.py`
- Test: `tests/worker/test_submission.py`
- Test: `tests/api/test_tasks.py`
- Create: `tests/services/test_task_quota.py`

**Interfaces:**
- `TaskRepository.count_active_for_user(user_id) -> int` counts exactly the seven active statuses from the design.
- `TaskRepository.lock_user(user_id) -> None` locks the existing user row for the current transaction.
- `TaskPersistenceService.create_task_with_result_for_subject(..., max_active_tasks: int | None = None) -> TaskCreationResult` checks idempotency before the limit.
- `TaskPersistenceService.retry_failed_task_for_subject(..., max_active_tasks: int | None = None) -> AnalysisTask` checks the owner quota before `FAILED -> QUEUED`.
- `TaskSubmissionService` receives `ResourceLimits` and passes its active-task limit to both new submission and retry paths.

- [ ] **Step 1: Write failing quota tests.**

Add these behaviors:

```python
def test_third_active_task_is_rejected_when_limit_is_two(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    service = TaskSubmissionService(
        persistence=persistence,
        broker=InMemoryTaskBroker(),
        resource_limits=ResourceLimits(max_active_tasks_per_user=2),
    )
    user_id = uuid4()

    service.submit(user_id=user_id, request=_request("one"))
    service.submit(user_id=user_id, request=_request("two"))

    with pytest.raises(ResourceQuotaExceededError) as raised:
        service.submit(user_id=user_id, request=_request("three"))

    assert raised.value.code == "TASK_CONCURRENCY_LIMIT"


def test_duplicate_idempotent_submission_does_not_consume_quota(uow_factory):
    persistence = TaskPersistenceService(uow_factory)
    broker = InMemoryTaskBroker()
    service = TaskSubmissionService(
        persistence=persistence,
        broker=broker,
        resource_limits=ResourceLimits(max_active_tasks_per_user=1),
    )
    user_id = uuid4()

    first = service.submit(user_id=user_id, request=_request("same"))
    second = service.submit(user_id=user_id, request=_request("same"))

    assert second.created is False
    assert second.task.task_id == first.task.task_id
    assert len(broker.messages) == 1
```

Add an API test asserting the third create returns HTTP 429 with `TASK_CONCURRENCY_LIMIT`, a `resource` detail of `active_tasks`, and the configured limit. Add a retry test proving a failed task cannot be requeued while the user already has the maximum number of active tasks.

- [ ] **Step 2: Run the focused quota tests and verify RED.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/services/test_task_quota.py tests/worker/test_submission.py tests/api/test_tasks.py -q
```

Expected: the new tests fail because `TaskSubmissionService` has no resource-limit dependency and the repository has no active-count/lock methods. Existing submission tests must remain collected.

- [ ] **Step 3: Implement transaction-safe quota checks.**

Add `lock_user()` to the user repository using `select(UserORM).where(UserORM.user_id == user_id).with_for_update()` and add `count_active_for_user()` to `TaskRepository` using `func.count()` and the exact active-status set. In `create_task_with_result_for_subject()`, ensure the user, lock the user, find the idempotent task, return it before quota evaluation when present, then count active tasks and raise `ResourceQuotaExceededError(code="TASK_CONCURRENCY_LIMIT", resource="active_tasks", ...)` before inserting.

Add the same limit check to `create_task_with_result()` for legacy user-ID callers. Add a locked owner lookup and active-count check to both retry methods before transitioning `FAILED` to `QUEUED`. Do not hold the user lock while publishing to the broker; the existing enqueue failure path must still mark the task failed.

Add `resource_limits: ResourceLimits | None = None` to `TaskSubmissionService`, defaulting to `ResourceLimits()`, and pass `max_active_tasks_per_user` to persistence. Keep old constructors valid. In `APIApplication.configure_task_services()`, inject `self.settings.resource_limits()`.

Map the quota exception in `tasks.py` to `APIError("TASK_CONCURRENCY_LIMIT", "active task limit reached", status_code=429, details=exc.details())`; add the mapping to create and retry routes. Do not include the exception string or database details.

- [ ] **Step 4: Run focused tests and a transaction regression.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/services/test_task_quota.py tests/worker/test_submission.py tests/api/test_tasks.py -q
E:\anaconda\python.exe -m pytest tests/database/test_task_lifecycle.py tests/integration -q
```

Expected: quota, idempotency, retry, and existing task lifecycle tests pass. Confirm the broker message list never receives a message for a rejected task.

- [ ] **Step 5: Commit submission quota enforcement.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/services/persistence.py src/data_analysis_agent/worker/service.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/application.py tests/services/test_task_quota.py tests/worker/test_submission.py tests/api/test_tasks.py
git commit -m "feat: enforce per-user active task quotas"

### Task 4: Enforce Model Budgets and Record LLM Metrics

**Files:**
- Modify: `src/data_analysis_agent/llm/client.py`
- Modify: `src/data_analysis_agent/services/llm.py`
- Modify: `src/data_analysis_agent/services/resource_budget.py`
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/agent/llm_port.py`
- Modify: `src/data_analysis_agent/worker/worker.py`
- Modify: `src/data_analysis_agent/worker/cli.py`
- Test: `tests/llm/test_client_chat.py`
- Test: `tests/llm/test_structured_output.py`
- Test: `tests/agent/test_public_api.py`
- Test: `tests/worker/test_worker.py`
- Create: `tests/agent/test_resource_budget_integration.py`

**Interfaces:**
- `LLMClient(..., before_call: Callable[[ChatRequest], None] | None = None)` invokes the hook once per logical `achat()`/`astream()` request before the provider, including each structured-output correction request.
- `LLMHelper(..., recorder: CallRecorder | None = None, before_call: BeforeCall | None = None)` forwards optional hooks to an internally created `LLMClient`.
- `TaskUsageRecorder(persistence, task_id)` implements `record(metrics)` by calling `TaskPersistenceService.record_model_usage()` with safe usage values.
- `AnalysisTaskWorker` creates `TaskBudget.from_usage(...)` and `TaskUsageRecorder` for each task and passes them to compatible Agent factories.
- `DataAnalysisAgent(..., resource_budget: TaskBudget | None = None, usage_recorder: CallRecorder | None = None)` keeps both arguments optional for old tests and public callers.

- [ ] **Step 1: Write failing pre-call and usage tests.**

Add a provider-call guard test:

```python
def test_before_call_hook_rejects_without_calling_provider():
    provider = FakeProvider([
        ProviderResponse(text="must not be used", provider="fake", model="chat")
    ])
    calls = []

    def reject(request):
        calls.append(request)
        raise ResourceQuotaExceededError(
            code="TASK_MODEL_CALL_LIMIT",
            resource="model_calls",
            limit=1,
            observed=1,
        )

    client = LLMClient(
        LLMConfig(api_key="key"),
        provider=provider,
        before_call=reject,
    )

    with pytest.raises(ResourceQuotaExceededError):
        asyncio.run(client.achat(request()))

    assert len(calls) == 1
    assert provider.calls == []
```

Add a structured-output test with an invalid first response and valid correction response; assert the usage recorder receives two `LLMCallMetrics` entries. Add a worker test with `ResourceQuotaExceededError` and assert the task becomes `FAILED`, `retry_scheduled` is false, and the broker receives no retry message.

- [ ] **Step 2: Run focused LLM and Worker tests and verify RED.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/llm/test_client_chat.py tests/llm/test_structured_output.py tests/agent/test_resource_budget_integration.py tests/worker/test_worker.py -q
```

Expected: the new tests fail because the hook and task usage recorder do not exist. Existing LLM tests must still collect and pass their pre-existing assertions where they do not exercise the new API.

- [ ] **Step 3: Implement hooks, recorder, and Worker/Agent wiring.**

Append the optional `before_call` parameter to `LLMClient.__init__` so positional compatibility is preserved. Invoke it after `_ensure_ready()` and effective-model construction but before the provider loop in both `achat()` and `astream()`. Do not invoke it for a missing-key configuration failure. Because `astructured_output()` calls `achat()` for the initial request and correction request, the hook and existing recorder naturally observe both.

Add optional `recorder` and `before_call` parameters to `LLMHelper`; pass them only when it creates its default `LLMClient`. In `DataAnalysisAgent`, when no custom `llm` is supplied, construct `LLMHelper(self.config, recorder=usage_recorder, before_call=resource_budget.check_model_call if resource_budget else None)`. Keep custom FakeLLM and legacy `call/parse_yaml_response` behavior unchanged.

Implement `TaskUsageRecorder.record()` with `metrics.usage` values defaulting to zero and `metrics.estimated_cost_usd` defaulting to zero. It must call persistence with non-negative finite values and never include the request or response text. Update the task-local budget after persistence succeeds; a persistence failure must propagate so the worker fails closed.

Add `resource_limits` to `AnalysisTaskWorker`. In `_create_agent()`, construct a budget from the claimed task counters and a task-bound recorder, then try an additional factory candidate containing `resource_budget` and `usage_recorder` before the existing compatibility candidates. In `worker/cli.py`, pass `settings.resource_limits()` to the Worker and let the existing `DataAnalysisAgent` factory accept the new optional arguments.

When a quota exception reaches Worker failure handling, use its stable `.code`, keep it non-retryable, and sanitize only the fixed message. Preserve existing retry behavior for `LLMError` and `RetryableTaskError`.

- [ ] **Step 4: Run focused tests and verify GREEN.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/llm/test_client_chat.py tests/llm/test_structured_output.py tests/agent/test_resource_budget_integration.py tests/worker/test_worker.py -q
E:\anaconda\python.exe -m pytest tests/agent/test_public_api.py tests/llm/test_public_api.py tests/contract/test_agent_contract.py -q
```

Expected: all focused and compatibility tests pass; the provider is never called after a pre-call rejection, correction requests are counted, and usage fields accumulate on the task.

- [ ] **Step 5: Commit model budget enforcement.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/llm/client.py src/data_analysis_agent/services/llm.py src/data_analysis_agent/services/resource_budget.py src/data_analysis_agent/agent/core.py src/data_analysis_agent/agent/llm_port.py src/data_analysis_agent/worker/worker.py src/data_analysis_agent/worker/cli.py tests/llm/test_client_chat.py tests/llm/test_structured_output.py tests/agent/test_public_api.py tests/agent/test_resource_budget_integration.py tests/worker/test_worker.py
git commit -m "feat: enforce task model budgets and usage accounting"
```

### Task 5: Apply Chart-Specific Resource Limits

**Files:**
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/services/resource_budget.py`
- Test: `tests/agent/test_execution_backend_integration.py`
- Create: `tests/agent/test_chart_resource_limits.py`

**Interfaces:**
- `TaskBudget.check_chart_output(file_bytes: int) -> None` checks one file against both per-file and cumulative limits.
- `DataAnalysisAgent._handle_collect_figures()` skips a rejected chart, appends a safe resource warning to its returned result, and never calls ArtifactStorage for that chart.
- Existing path-boundary, evidence, report, and ArtifactStorage behavior remains unchanged for accepted charts.

- [ ] **Step 1: Write failing chart-limit tests.**

Use a temporary output file and an injected `TaskBudget`:

```python
def test_collect_figures_omits_chart_over_file_limit(tmp_path):
    chart_path = tmp_path / "large.png"
    chart_path.write_bytes(b"12345678901")
    agent = make_agent_with_budget(
        tmp_path,
        ResourceLimits(max_chart_file_bytes=10, max_chart_total_bytes=100),
    )
    agent._resolve_executor_path = lambda value: str(chart_path)

    result = agent._handle_collect_figures(
        "response",
        {"figures_to_collect": [{"figure_number": 1, "file_path": "large.png"}]},
    )

    assert result["collected_figures"] == []
    assert result["resource_warnings"] == [
        {"code": "TASK_CHART_RESOURCE_LIMIT", "resource": "chart_file_bytes"}
    ]


def test_collect_figures_accepts_charts_until_total_limit(tmp_path):
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(b"1234")
    second.write_bytes(b"56789")
    agent = make_agent_with_budget(
        tmp_path,
        ResourceLimits(max_chart_file_bytes=10, max_chart_total_bytes=8),
    )
    agent._resolve_executor_path = lambda value: value

    result = agent._handle_collect_figures(
        "response",
        {"figures_to_collect": [
            {"figure_number": 1, "file_path": str(first)},
            {"figure_number": 2, "file_path": str(second)},
        ]},
    )

    assert [item["file_path"] for item in result["collected_figures"]] == [str(first)]
    assert result["resource_warnings"][0]["resource"] == "chart_total_bytes"
```

- [ ] **Step 2: Run the chart tests and verify RED.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/agent/test_chart_resource_limits.py tests/agent/test_execution_backend_integration.py -q
```

Expected: the new tests fail because figure collection currently has no budget check or warning payload.

- [ ] **Step 3: Implement bounded chart collection.**

In `_handle_collect_figures()`, resolve and validate the file path first. For an existing regular file, read only `stat().st_size`, call `resource_budget.check_chart_output(size)`, and on `ResourceQuotaExceededError` append a warning containing only `code` and `resource`, then continue. Call the existing evidence registry and append the collection payload only after the budget check passes. Preserve the current behavior for missing paths and path traversal. Initialize `resource_warnings` as an empty list so legacy result consumers receive a stable list.

Do not add chart paths or exception strings to the warning. Do not change `_store_figure_artifacts()`; rejected charts never reach it, while accepted charts keep the existing trusted-root check and storage behavior.

- [ ] **Step 4: Run chart and report regression tests.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/agent/test_chart_resource_limits.py tests/agent/test_execution_backend_integration.py tests/agent/test_report_service_integration.py tests/reports -q
```

Expected: chart limits, accepted chart storage, path safety, evidence registration, and report generation all pass.

- [ ] **Step 5: Commit chart resource limits.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/agent/core.py src/data_analysis_agent/services/resource_budget.py tests/agent/test_chart_resource_limits.py tests/agent/test_execution_backend_integration.py
git commit -m "feat: bound chart resource usage"

### Task 6: Expose Usage, Document Configuration, and Close Integration Seams

**Files:**
- Modify: `src/data_analysis_agent/api/schemas.py`
- Modify: `src/data_analysis_agent/api/routers/tasks.py`
- Modify: `src/data_analysis_agent/api/application.py`
- Modify: `src/data_analysis_agent/api/app.py`
- Modify: `src/data_analysis_agent/worker/service.py`
- Modify: `README.md`
- Modify: `docs/deployment/local-compose.md`
- Test: `tests/api/test_schemas.py`
- Test: `tests/api/test_tasks.py`
- Test: `tests/worker/test_submission.py`
- Test: `tests/contract/test_agent_contract.py`

**Interfaces:**
- `TaskUsageResponse` contains the six safe usage fields and rejects unknown fields.
- `AnalysisTaskResponse.usage` is always present with zero defaults for old tasks.
- `APIApplication.from_settings()` and `configure_task_services()` use one `ResourceLimits` instance per application.
- API quota errors are HTTP 429; runtime quota errors appear in task `error.code` without raw diagnostics.

- [ ] **Step 1: Write failing public-contract tests.**

Add a schema test:

```python
def test_task_response_exposes_safe_usage_summary():
    response = AnalysisTaskResponse(
        task_id=uuid4(),
        query="分析销售",
        status=TaskStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        usage=TaskUsageResponse(
            model_call_count=2,
            model_input_tokens=10,
            model_output_tokens=5,
            model_total_tokens=15,
            model_duration_ms=120,
            estimated_model_cost_usd=0.0004,
        ),
    )

    assert response.model_dump(mode="json")["usage"]["model_total_tokens"] == 15
```

Add an API test that creates two active tasks with a configured limit of one and asserts the second response is `429`, has code `TASK_CONCURRENCY_LIMIT`, and does not contain database or broker exception text. Add a detail test asserting task usage values returned by the persistence layer appear in the API response.

- [ ] **Step 2: Run the public-contract tests and verify RED.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_schemas.py tests/api/test_tasks.py tests/contract/test_agent_contract.py -q
```

Expected: collection or assertion failures because `TaskUsageResponse` and the response mapping do not exist. Existing API contract tests must remain collected.

- [ ] **Step 3: Implement API mapping and documentation.**

Add `TaskUsageResponse` with non-negative integer counters, non-negative duration, and non-negative cost. Add `usage` to `AnalysisTaskResponse` with a default zero summary. Update `_task_response()` to map the six task fields and preserve the quota mapping from Task 3. Add a dedicated response test through the actual FastAPI route rather than asserting exception internals. Update README and local-compose documentation with the six environment variables, default values, the fact that cost is estimated when provider usage/prices are unavailable, and the HTTP 429/runtime error behavior. Do not document or expose prompts, responses, or paths.

- [ ] **Step 4: Run focused API, Worker, compatibility, and documentation checks.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_schemas.py tests/api/test_tasks.py tests/worker/test_submission.py tests/contract/test_agent_contract.py -q
rg -n "MAX_ACTIVE_TASKS_PER_USER|MAX_MODEL_CALLS_PER_TASK|MAX_CHART_COUNT|TASK_CONCURRENCY_LIMIT|model_total_tokens" README.md docs/deployment/local-compose.md src tests
```

Expected: all focused tests pass and the documented names match the implemented configuration and response fields.

- [ ] **Step 5: Commit the public integration slice.**

Run:

```powershell
git diff --check
git add src/data_analysis_agent/api/schemas.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/application.py src/data_analysis_agent/api/app.py src/data_analysis_agent/worker/service.py README.md docs/deployment/local-compose.md tests/api/test_schemas.py tests/api/test_tasks.py tests/worker/test_submission.py tests/contract/test_agent_contract.py
git commit -m "feat: expose task resource usage and quota errors"
```

### Task 7: Full Verification and M23-A Handoff

**Files:**
- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `findings.md`
- Test/inspect: all M23-A files listed above

**Interfaces:**
- No new production interface; this task verifies the complete M23-A contract and records evidence for the next M23 phase.

- [ ] **Step 1: Re-read the implementation plan and create the completion checklist.**

Confirm each M23-A requirement against the plan: bounded active tasks, idempotent duplicate exemption, atomic six-field usage accumulation, pre-provider model-call rejection, structured correction counting, optional cost cap, chart count/file/total limits, safe API usage, non-retryable quota errors, and unchanged broker payloads.

- [ ] **Step 2: Run the focused M23-A suite.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/config/test_resource_settings.py tests/services/test_resource_budget.py tests/services/test_task_quota.py tests/database/test_task_resource_usage.py tests/llm/test_client_chat.py tests/llm/test_structured_output.py tests/agent/test_resource_budget_integration.py tests/agent/test_chart_resource_limits.py tests/worker/test_submission.py tests/worker/test_worker.py tests/api/test_schemas.py tests/api/test_tasks.py -q
```

Expected: all M23-A tests pass with no new failures. Record any pre-existing warnings or skips, including their exact reason, in `progress.md`.

- [ ] **Step 3: Run repository-wide verification.**

Run:

```powershell
E:\anaconda\python.exe -m pytest -q
E:\anaconda\python.exe -m compileall -q src
git diff --check
```

Expected: pytest exits 0, compileall exits 0, and diff check emits no whitespace errors. If a failure is found, add it to the `Errors Encountered` table before attempting a fix; do not mark M23-A complete from a partial suite.

- [ ] **Step 4: Verify database migration and public compatibility.**

Run:

```powershell
E:\anaconda\python.exe -m alembic upgrade head
E:\anaconda\python.exe -m pytest tests/database tests/public_api tests/contract -q
```

Expected: the new migration is at head, repeated upgrade is idempotent, and existing public/contract tests pass.

- [ ] **Step 5: Update persistent planning records.**

Append to `progress.md` the commits, focused/full test counts, migration result, and any warnings. Update the M23-A section in `task_plan.md` from `in_progress` to `complete` only after the verification commands above pass. Add any unresolved follow-up, especially M23-B data summarization, to `findings.md` without putting external or raw model content in the plan file.

- [ ] **Step 6: Record the verification evidence.**

Run:

```powershell
git diff --check
```

Append the M23-A verification entry to the three planning files with `apply_patch`. These files already contain unrelated M22 working-tree changes, so do not run a whole-file `git add` or commit for them. If the final integration workflow supports explicit hunk staging, stage only the new M23-A lines; otherwise leave the additive planning records unstaged and report that state. Existing M22 `sdd/` reports and worktree artifacts must remain untouched.

## Plan Self-Review

- Spec coverage: M23-A tasks cover active-task quotas, task usage/cost accounting, model-call budgets, chart resource limits, safe API reporting, concurrency isolation, and explicit quota errors. M23-B and M23-C remain separate follow-up phases as required by the approved design.
- Placeholder scan: the plan contains no open placeholder token or unspecified test step; each production change has an explicit failing test, command, expected result, implementation boundary, and verification command.
- Type consistency: `ResourceLimits`, `TaskBudget`, `ResourceQuotaExceededError`, `TaskUsageRecorder`, `TaskPersistenceService.record_model_usage()`, and `TaskUsageResponse` are defined before their consumers and use the same field names throughout.
- Compatibility: old `LLMClient`/`LLMHelper` positional constructors, old `TaskSubmissionService` constructors, `record_model_call()`, custom Agent factories, and legacy FakeLLM paths remain supported through optional parameters and delegating wrappers.
- Worktree safety: the final planning-file update is additive and must not stage existing M22 changes in `task_plan.md`, `progress.md`, or `findings.md`.
