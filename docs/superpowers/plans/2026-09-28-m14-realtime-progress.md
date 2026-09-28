# M14 实时进度推送实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 FastAPI、SQLAlchemy 和 Worker 任务链路上增加可持久化、可断线续传的实时分析进度 SSE。

**Architecture:** 继续使用 `task_events` 作为唯一事实源，为事件增加任务内单调 `sequence`、阶段和进度字段。Worker/Agent 通过统一事件回调写入安全摘要，API 先按所有权授权，再从数据库回放 `Last-Event-ID` 之后的事件并轮询新事件；终态事件发送后结束连接。

**Tech Stack:** Python 3.10+、Pydantic 2、SQLAlchemy 2、Alembic、FastAPI/Starlette `StreamingResponse`、pytest、SQLite/MySQL 兼容迁移。

## Global Constraints

- 第一版使用 SSE，不引入 WebSocket、Redis Pub/Sub 或新的实时消息依赖。
- `task_events` 是事件事实源；SSE 不依赖内存广播，API/Worker 重启后必须能从数据库恢复。
- SSE `id` 使用任务内 `sequence`；重连只发送 `sequence > Last-Event-ID` 的事件，并按 `sequence ASC` 排序。
- 事件公开内容必须包含 `event_id`、`task_id`、`event_type`、`timestamp`、`stage`、`message`、`progress` 和 `metadata`，并额外包含 `sequence`。
- 不得通过事件暴露 API Key、凭据、prompt/response、代码、原始执行输出、原始数据、本地路径或存储 URI。
- 任务事件接口和 SSE 都必须按认证用户进行任务所有权校验；开发/测试继续使用现有身份提供器。
- 保留旧 `TaskEventType` 值、领域状态机契约和分页 `/events` 接口。
- 不新增前端工程；交付后端协议、测试和必要的 README 使用说明。
- 每个实现任务必须遵循 RED → GREEN → REFACTOR，并在任务结束时运行该任务的聚焦测试。

---

### Task 1: 建立 M14 事件与安全公开契约

**Files:**
- Modify: `src/data_analysis_agent/domain/enums.py`
- Modify: `src/data_analysis_agent/domain/models.py:318-330`
- Modify: `src/data_analysis_agent/persistence/models.py:51-61`
- Modify: `src/data_analysis_agent/persistence/mappers.py:event_to_record/record_to_event`
- Modify: `src/data_analysis_agent/api/schemas.py:1-150`
- Create: `src/data_analysis_agent/services/progress.py`
- Create: `tests/domain/test_progress_events.py`
- Create: `tests/api/test_progress_schemas.py`

**Interfaces:**
- `TaskEventType` continues to expose `STATUS_CHANGED`, `TOOL_CALLED`, `EXECUTION_COMPLETED`, `ARTIFACT_CREATED`, and `ERROR`, and adds the eleven M14 dot-value members: `TASK_CREATED`, `TASK_STARTED`, `STAGE_STARTED`, `LLM_STARTED`, `LLM_COMPLETED`, `TOOL_STARTED`, `TOOL_COMPLETED`, `CHART_CREATED`, `REPORT_CREATED`, `TASK_FAILED`, and `TASK_COMPLETED`.
- `TaskEvent` and `TaskEventRecord` add `sequence: int | None`, `stage: str | None`, and `progress: float | None`; the optional domain defaults keep existing direct test construction compatible until persistence assigns values.
- `TaskProgressEventResponse` has `event_id: UUID`, `task_id: UUID`, `event_type: TaskEventType`, `timestamp: datetime`, `sequence: StrictInt`, `stage: StrictStr | None`, `message: StrictStr | None`, `progress: float`, and `metadata: dict[str, Any]`.
- `data_analysis_agent.services.progress` exports `STAGE_PROGRESS`, `M14_EVENT_TYPES`, `project_event(event: TaskEvent) -> TaskEvent`, `safe_event_metadata(event_type, metadata) -> dict[str, Any]`, and `safe_event_message(message, *, secrets=()) -> str | None`.

- [ ] **Step 1: Write failing domain and API contract tests**

```python
# tests/domain/test_progress_events.py
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import TaskEvent


def test_m14_event_type_values_are_stable():
    assert TaskEventType.TASK_CREATED.value == "task.created"
    assert TaskEventType.TASK_COMPLETED.value == "task.completed"
    assert TaskEventType.LLM_STARTED.value == "llm.started"


def test_task_event_accepts_sequence_stage_and_progress():
    event = TaskEvent(
        task_id=uuid4(),
        event_type=TaskEventType.STAGE_STARTED,
        to_status=TaskStatus.ANALYZING,
        sequence=4,
        stage="ANALYZING",
        progress=55,
    )

    assert event.sequence == 4
    assert event.stage == "ANALYZING"
    assert event.progress == 55


def test_task_event_rejects_invalid_sequence_and_progress():
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id=uuid4(),
            event_type=TaskEventType.STAGE_STARTED,
            to_status=TaskStatus.ANALYZING,
            sequence=0,
        )
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id=uuid4(),
            event_type=TaskEventType.STAGE_STARTED,
            to_status=TaskStatus.ANALYZING,
            progress=101,
        )
```

```python
# tests/api/test_progress_schemas.py
from datetime import datetime, timezone
from uuid import uuid4

from data_analysis_agent.api.schemas import TaskProgressEventResponse
from data_analysis_agent.domain.enums import TaskEventType


def test_progress_response_uses_timestamp_and_filters_private_metadata():
    response = TaskProgressEventResponse(
        event_id=uuid4(),
        task_id=uuid4(),
        event_type=TaskEventType.TASK_CREATED,
        timestamp=datetime.now(timezone.utc),
        sequence=1,
        stage=None,
        message="task created",
        progress=0,
        metadata={"artifact_id": "safe", "file_path": "C:/private/a.csv"},
    )

    payload = response.model_dump(mode="json")
    assert payload["progress"] == 0
    assert "file_path" not in str(payload)
    assert payload["metadata"]["artifact_id"] == "safe"
```

- [ ] **Step 2: Run the new tests and verify they fail for missing M14 fields**

Run:

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/domain/test_progress_events.py tests/api/test_progress_schemas.py
```

Expected: collection or validation failures because the new enum members, event fields, response model, and progress sanitizer do not exist yet.

- [ ] **Step 3: Add the enum values, event fields, DTO, and safe projection helpers**

Add the M14 values without removing legacy values. Extend `TaskEvent` and `TaskEventRecord` with positive `sequence`, optional `stage`, and bounded `progress`. Add the DTO with a `timestamp` field instead of leaking the persistence name `occurred_at`.

Implement `safe_event_metadata` as a recursive mapping/list/tuple filter with maximum depth 4, at most 64 mapping keys or sequence items at each level, and a 16 KiB serialized payload cap. The blocked key set must include `api_key`, `access_token`, `authorization`, `password`, `secret`, `token`, `prompt`, `response`, `code`, `arguments`, `output`, `raw`, `file_path`, `source_uri`, and `storage_uri`. Keep only JSON-compatible values, cap strings at 240 characters, and replace an over-limit collection with a fixed `"[truncated]"` marker. `safe_event_message` must call `sanitize_text`, replace empty messages with `None`, and cap a non-empty message at 240 characters.

`project_event` must fill missing compatibility values using `TaskStatus`: use `STAGE_PROGRESS = {RUNNING: 5, EXPLORING: 15, CLEANING: 30, ANALYZING: 55, VALIDATING: 75, REPORTING: 90, COMPLETED: 100}`; map an initial `PENDING` event to `task.created`, a `RUNNING` target to `task.started`, active stage targets to `stage.started`, `FAILED` to `task.failed`, and `COMPLETED` to `task.completed`; preserve queue/retry/cancel legacy event types. The function must return a copied event and never mutate the stored domain object.

- [ ] **Step 4: Run focused tests and the existing model/schema regression tests**

Run:

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/domain/test_progress_events.py tests/api/test_progress_schemas.py tests/domain/test_models.py tests/api/test_schemas.py tests/persistence/test_mappers.py
```

Expected: all new tests and existing domain/API/persistence contract tests pass, with no legacy `STATUS_CHANGED` assertions changed.

- [ ] **Step 5: Commit the contract layer**

```powershell
git add src/data_analysis_agent/domain/enums.py src/data_analysis_agent/domain/models.py src/data_analysis_agent/persistence/models.py src/data_analysis_agent/persistence/mappers.py src/data_analysis_agent/api/schemas.py src/data_analysis_agent/services/progress.py tests/domain/test_progress_events.py tests/api/test_progress_schemas.py
git commit -m "feat: add M14 progress event contract"
```

### Task 2: Persist ordered events and add the M14 migration

**Files:**
- Modify: `src/data_analysis_agent/persistence/orm_models.py:102-132`
- Modify: `src/data_analysis_agent/persistence/orm_mappers.py:event_orm_to_record/event_record_to_orm`
- Modify: `src/data_analysis_agent/persistence/repositories.py:383-425`
- Create: `alembic/versions/20260928_0006_realtime_progress_events.py`
- Modify: `tests/database/test_core_repositories.py`
- Modify: `tests/database/test_sqlalchemy_mappers.py`
- Modify: `tests/database/test_schema.py`
- Create: `tests/database/test_progress_events.py`

**Interfaces:**
- `TaskEventRepository.append(event: TaskEvent) -> TaskEvent` assigns the next task-local sequence when `event.sequence is None` and returns the event with its assigned sequence.
- `TaskEventRepository.list_after(task_id: UUID, *, sequence: int, limit: int) -> list[TaskEvent]` returns only rows with `sequence > sequence`, ordered by sequence.
- `TaskEventRepository.latest_sequence(task_id: UUID) -> int` returns zero when the task has no events.
- `TaskEventORM` stores `sequence` as a non-null integer, `stage` as a nullable bounded string, and `progress` as a nullable float with a database check between 0 and 100.

- [ ] **Step 1: Write failing repository and migration tests**

```python
# tests/database/test_progress_events.py
from uuid import uuid4

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import AnalysisTask, TaskEvent
from data_analysis_agent.persistence.models import UserRecord


def test_event_repository_assigns_task_local_sequences(uow_factory):
    task_id = uuid4()
    user_id = uuid4()
    now = AnalysisTask(query="q").created_at
    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.tasks.add(
            user_id=user_id,
            task=AnalysisTask(task_id=task_id, query="q", created_at=now, updated_at=now),
            idempotency_key="sequence-key",
            request_hash="hash",
        )
        first = uow.task_events.append(
            TaskEvent(task_id=task_id, event_type=TaskEventType.TASK_CREATED, to_status=TaskStatus.PENDING)
        )
        second = uow.task_events.append(
            TaskEvent(task_id=task_id, event_type=TaskEventType.STAGE_STARTED, to_status=TaskStatus.ANALYZING)
        )
        uow.commit()

    assert (first.sequence, second.sequence) == (1, 2)
```

```python
# tests/database/test_schema.py
def test_m14_migration_adds_ordered_progress_columns(tmp_path: Path):
    database_path = tmp_path / "m14-progress.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    migration_engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(migration_engine)
    columns = {column["name"] for column in inspector.get_columns("task_events")}
    assert {"sequence", "stage", "progress"} <= columns
    assert any(index["name"] == "uq_task_events_task_sequence" for index in inspector.get_indexes("task_events"))
    migration_engine.dispose()
```

- [ ] **Step 2: Run the tests to verify they fail before schema/repository changes**

Run:

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/database/test_progress_events.py tests/database/test_schema.py::test_m14_migration_adds_ordered_progress_columns
```

Expected: failure because the ORM has no M14 columns and the repository has no sequence/list-after methods.

- [ ] **Step 3: Extend records, ORM mappings, and ordered repository queries**

Add `sequence`, `stage`, and `progress` to `event_orm_to_record` and `event_record_to_orm`. In `append`, compute `max(sequence) + 1` inside the current transaction when the domain event has no sequence; retain an explicit positive sequence for migration/fixture use. Query all event pages and `list_for_task` by `TaskEventORM.sequence`, not timestamp. Add `list_after` and `latest_sequence` with the same mapping error handling as existing event reads.

The task service already locks the task row before lifecycle writes. Keep that lock as the concurrency boundary for sequence allocation, and retain the unique `(task_id, sequence)` constraint as the database safety net.

Update every existing direct `TaskEventORM` fixture insert in `tests/database/test_schema.py` and `tests/database/test_sqlalchemy_mappers.py` to supply a deterministic sequence after the new column becomes non-null. Inserts used before the M14 migration must remain on the legacy schema and need no new column.

- [ ] **Step 4: Add the Alembic revision and deterministic backfill**

Create `20260928_0006_realtime_progress_events.py` with `revision = "20260928_0006"` and `down_revision = "20260927_0005"`. The upgrade must:

1. Add nullable `sequence`, `stage`, and `progress` columns.
2. Re-read each task's existing events ordered by `occurred_at, event_id` and update their sequence values starting at 1.
3. Backfill `progress` from `to_status` for existing status events and leave `stage` null for non-stage events.
4. Replace the event-type check constraint with one containing both legacy and M14 values while retaining the legal `STATUS_CHANGED` transition expression.
5. Make `sequence` non-null, add the 0-100 progress check, and add unique index `uq_task_events_task_sequence` plus `ix_task_events_task_sequence`.

The downgrade must drop the new indexes/checks/columns and restore the previous event-type constraint. It must refuse to downgrade if a row uses an M14 event type or has non-null stage/progress data that cannot be represented by the previous schema. Test both upgrade and downgrade on SQLite; keep the migration SQL portable to MySQL by using Alembic batch operations and bound parameters.

- [ ] **Step 5: Run persistence and migration verification**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/database/test_progress_events.py tests/database/test_core_repositories.py tests/database/test_sqlalchemy_mappers.py tests/database/test_schema.py tests/database/test_task_lifecycle.py
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-migration-check.sqlite upgrade head
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-migration-check.sqlite downgrade 20260927_0005
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-migration-check.sqlite upgrade head
```

Expected: ordered repository tests pass; upgrade/downgrade/upgrade complete without schema errors. Remove only the explicitly created `m14-migration-check.sqlite` after verification.

- [ ] **Step 6: Commit persistence and migration**

```powershell
git add src/data_analysis_agent/persistence/orm_models.py src/data_analysis_agent/persistence/orm_mappers.py src/data_analysis_agent/persistence/repositories.py alembic/versions/20260928_0006_realtime_progress_events.py tests/database/test_progress_events.py tests/database/test_core_repositories.py tests/database/test_sqlalchemy_mappers.py tests/database/test_schema.py
git commit -m "feat: persist ordered progress events"
```

### Task 3: Map lifecycle transitions and expose owner-scoped history queries

**Files:**
- Modify: `src/data_analysis_agent/services/persistence.py`
- Modify: `src/data_analysis_agent/services/progress.py`
- Modify: `tests/database/test_task_lifecycle.py`
- Modify: `tests/worker/test_submission.py`
- Modify: `tests/api/test_tasks.py`
- Create: `tests/services/test_progress_persistence.py`

**Interfaces:**
- `TaskPersistenceService.append_progress_event(*, task_id: UUID, event_type: TaskEventType, stage: TaskStatus | None = None, message: str | None = None, metadata: Mapping[str, Any] | None = None, progress: float | None = None, occurred_at: datetime | None = None) -> TaskEvent` locks the task, sanitizes the public fields, assigns a sequence, appends the event, commits, and returns it.
- `TaskPersistenceService.list_events_after_for_user(task_id: UUID, user_id: UUID, *, sequence: int, limit: int = 100) -> list[TaskEvent]` verifies ownership and returns ordered events after the cursor.
- Existing `list_events_for_user` continues to return `(events, total)` and now returns sequence order with `project_event` applied for legacy rows.

- [ ] **Step 1: Write failing lifecycle and security tests**

```python
# tests/services/test_progress_persistence.py
from uuid import UUID, uuid4

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.services.persistence import TaskPersistenceService


def test_lifecycle_events_have_stable_types_progress_and_sequences(uow_factory):
    service = TaskPersistenceService(uow_factory)
    task = service.create_task(
        user_id=uuid4(),
        request=AnalysisTaskCreateRequest(query="分析", idempotency_key="progress-key"),
    )
    queued, _ = service.transition_task(task_id=task.task_id, target=TaskStatus.QUEUED)
    claim = service.claim_task(task.task_id)
    service.record_stage_transition(task_id=task.task_id, target=TaskStatus.ANALYZING)

    with uow_factory() as uow:
        events = uow.task_events.list_for_task(task.task_id)

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type is TaskEventType.TASK_CREATED
    assert claim.claimed is True
    assert claim.task.status is TaskStatus.RUNNING
    assert any(event.event_type is TaskEventType.STAGE_STARTED and event.progress == 55 for event in events)


def test_progress_event_removes_secrets_code_raw_output_and_paths(uow_factory):
    service = TaskPersistenceService(uow_factory)
    task = service.create_task(
        user_id=uuid4(),
        request=AnalysisTaskCreateRequest(query="分析", idempotency_key="redaction-key"),
    )
    event = service.append_progress_event(
        task_id=task.task_id,
        event_type=TaskEventType.LLM_COMPLETED,
        metadata={
            "api_key": "SECRET",
            "code": "print(1)",
            "output": "raw data",
            "file_path": "C:/private/chart.png",
            "duration_ms": 12,
        },
    )

    assert "SECRET" not in str(event.metadata)
    assert "print(1)" not in str(event.metadata)
    assert event.metadata == {"duration_ms": 12}
```

- [ ] **Step 2: Run focused tests and verify they fail**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/services/test_progress_persistence.py tests/database/test_task_lifecycle.py tests/worker/test_submission.py
```

Expected: failure because lifecycle writes still use legacy event types, no append-progress method exists, and sequence/progress metadata is not populated.

- [ ] **Step 3: Implement lifecycle event projection and owner-scoped event queries**

Add a private helper in `TaskPersistenceService` that copies a state event and sets M14 type/stage/progress without changing `transition_task` itself. Use it at these write points:

- `create_task_with_result`: initial PENDING event becomes `task.created`, progress 0.
- `claim_task`: QUEUED → RUNNING becomes `task.started`, progress 5.
- `transition_task` and `record_stage_transition`: active target becomes `stage.started`; FAILED becomes `task.failed`; COMPLETED becomes `task.completed`; queue/retry/cancel preserve legacy status event type.
- `fail_task`: metadata contains only `error_code` plus safe retry/attempt fields; event message uses `safe_event_message`.

Implement `append_progress_event` with `get_for_update`, `_next_event_time`, current task status, safe event fields, repository append, and one commit. It must reject terminal-task observation events only when the event would be a new lifecycle mutation; `llm.completed`, `tool.completed`, `chart.created`, and `report.created` already committed before a task can become terminal.

Implement `list_events_after_for_user` with owner lookup before `list_after`. Change history pagination to use `sequence` and project old rows before returning them. Keep the return shape and existing error classes.

- [ ] **Step 4: Run task/API history regression tests**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/services/test_progress_persistence.py tests/database/test_task_lifecycle.py tests/worker/test_submission.py tests/api/test_tasks.py tests/api/test_schemas.py
```

Expected: all tests pass; existing API assertions for `to_status`, event totals, retry, cancel, and owner isolation remain valid, while new assertions verify `task.created`, `task.started`, `stage.started`, safe metadata, and increasing sequence.

- [ ] **Step 5: Commit lifecycle persistence**

```powershell
git add src/data_analysis_agent/services/persistence.py src/data_analysis_agent/services/progress.py tests/services/test_progress_persistence.py tests/database/test_task_lifecycle.py tests/worker/test_submission.py tests/api/test_tasks.py
git commit -m "feat: emit persisted task progress lifecycle events"
```

### Task 4: Emit LLM, tool, chart, and report progress events from the Worker path

**Files:**
- Modify: `src/data_analysis_agent/agent/llm_port.py`
- Modify: `src/data_analysis_agent/agent/orchestrator.py`
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/worker/worker.py:170-190,321-345`
- Modify: `src/data_analysis_agent/worker/cli.py:40-65`
- Modify: `tests/llm/test_agent_structured_boundary.py`
- Modify: `tests/agent/test_orchestrator_tools.py`
- Modify: `tests/worker/test_worker.py`
- Create: `tests/agent/test_progress_callbacks.py`

**Interfaces:**
- Add `ProgressEventCallback = Callable[[TaskEvent], None]` in `agent/orchestrator.py` or a shared type module.
- `AgentLLMPort.__init__(helper, config, *, task_id_getter=None, stage_getter=None, event_callback=None)` keeps all new parameters optional and emits safe `LLM_STARTED`/`LLM_COMPLETED` events around structured and legacy calls; `stage_getter` supplies `to_status`/`stage`, falling back to `RUNNING` when unavailable.
- `AgentOrchestrator.__init__(..., event_callback: ProgressEventCallback | None = None)` keeps `transition_callback` unchanged and emits safe `TOOL_STARTED`/`TOOL_COMPLETED` events around synchronous tool execution while preserving the existing in-memory `TOOL_CALLED` event.
- `DataAnalysisAgent.__init__(..., progress_callback: ProgressEventCallback | None = None)` keeps the public compatibility signature additive; the callback is passed to the LLM port, orchestrator, and artifact/report hooks.
- `AnalysisTaskWorker._on_agent_event(event: TaskEvent)` calls `append_progress_event` and catches/logs callback persistence failures without exposing exception text or aborting a valid analysis.

- [ ] **Step 1: Write failing callback tests**

```python
# tests/agent/test_progress_callbacks.py
from uuid import uuid4

from data_analysis_agent.agent.llm_port import AgentLLMPort
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.domain.enums import TaskEventType


class StructuredHelper:
    def structured_output(self, request):
        return {"action": "generate_code", "code": "print(1)"}


def test_llm_port_emits_start_and_completion_without_prompt_or_response():
    events = []
    task_id = uuid4()
    port = AgentLLMPort(
        StructuredHelper(),
        LLMConfig(api_key="secret-key"),
        task_id_getter=lambda: task_id,
        event_callback=events.append,
    )

    port.request_action("private prompt")

    assert [event.event_type for event in events] == [
        TaskEventType.LLM_STARTED,
        TaskEventType.LLM_COMPLETED,
    ]
    assert "private prompt" not in str(events)
    assert "secret-key" not in str(events)
```

```python
# Add this test to the existing tests/agent/test_orchestrator_tools.py file;
# it already defines _registry/_handlers and imports the orchestration helpers.
def test_orchestrator_emits_tool_start_and_completion_without_arguments():
    events = []
    seen = []
    task = AnalysisTask(query="progress tool")
    handlers = _handlers()

    def validate(stage_input, call_tool):
        return StageResult(output=call_tool("validate_metric", {"value": 7}).output)

    handlers[TaskStatus.ANALYZING] = validate
    orchestrator = AgentOrchestrator(
        task=task,
        handlers=handlers,
        tool_executor=ToolExecutor(_registry(seen)),
        event_callback=events.append,
    )
    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert [event.event_type for event in events] == [
        TaskEventType.TOOL_STARTED,
        TaskEventType.TOOL_COMPLETED,
    ]
    assert "private" not in str(events)
```

- [ ] **Step 2: Run callback tests and verify the event callbacks are absent**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/agent/test_progress_callbacks.py tests/llm/test_agent_structured_boundary.py tests/agent/test_orchestrator_tools.py
```

Expected: failure because `AgentLLMPort` and `AgentOrchestrator` do not accept or invoke the new callback.

- [ ] **Step 3: Add LLM and tool lifecycle callbacks**

Wrap each LLM request in a start/finish pair. Use `monotonic()` for duration, `TaskEventType.LLM_*`, the task ID getter, and only `{"duration_ms": int, "success": bool, "error_code": str | None}` metadata. Emit completion in an exception path before re-raising the original exception. The callback wrapper must swallow callback exceptions so an observer cannot change the existing model behavior.

In `AgentOrchestrator.call_tool`, emit `TOOL_STARTED` immediately before `tool_executor.execute` and `TOOL_COMPLETED` in a `finally` block with tool name, status, duration, and safe error code only. Keep `_record_tool_result` and its legacy `TOOL_CALLED` state event unchanged for current tests.

- [ ] **Step 4: Connect Worker callbacks and emit artifact/report events**

Add `on_event` to the Worker agent-factory candidate kwargs and pass it from `worker/cli.py` to `DataAnalysisAgent(progress_callback=...)`. In `DataAnalysisAgent`, make the callback context-aware through task/stage getters so an agent run that creates a new compatibility task ID still emits the correct task ID.

After `_store_figure_artifacts` successfully registers each chart, emit `CHART_CREATED` with only `artifact_id`, `format`/MIME type, and current stage. After each successful report artifact is registered in `_generate_final_report`, emit `REPORT_CREATED` with only `artifact_id` and report format. Do not emit an artifact event for a failed storage operation.

Implement `AnalysisTaskWorker._on_agent_event` as the persistence bridge. It must call `TaskPersistenceService.append_progress_event` with the event fields, and on `PersistenceError` log only a fixed message such as `progress event persistence failed`; never log `event.metadata`, exception text, prompt, code, or paths.

- [ ] **Step 5: Run Agent/Worker/LLM regression tests**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/agent tests/llm/test_agent_structured_boundary.py tests/worker tests/integration/test_analysis_flow.py tests/storage/test_agent_storage_integration.py
```

Expected: existing legacy in-memory event assertions remain green; new tests prove all M14 observation event types are emitted, event metadata excludes code/raw data/secrets, and observer persistence failure does not change analysis status.

- [ ] **Step 6: Commit Agent and Worker instrumentation**

```powershell
git add src/data_analysis_agent/agent/llm_port.py src/data_analysis_agent/agent/orchestrator.py src/data_analysis_agent/agent/core.py src/data_analysis_agent/worker/worker.py src/data_analysis_agent/worker/cli.py tests/agent/test_progress_callbacks.py tests/agent/test_orchestrator_tools.py tests/llm/test_agent_structured_boundary.py tests/worker/test_worker.py
git commit -m "feat: publish analysis progress from agent and worker"
```

### Task 5: Add the owner-scoped SSE stream and resumable API surface

**Files:**
- Create: `src/data_analysis_agent/api/progress_stream.py`
- Modify: `src/data_analysis_agent/api/schemas.py:115-175`
- Modify: `src/data_analysis_agent/api/routers/tasks.py`
- Modify: `src/data_analysis_agent/api/__init__.py`
- Modify: `tests/api/test_tasks.py`
- Modify: `tests/api/test_app.py`
- Create: `tests/api/test_progress_stream.py`

**Interfaces:**
- `parse_last_event_id(value: str | None) -> int` returns `0` for no header and raises `InvalidLastEventID` for empty, non-numeric, or non-positive header values.
- `encode_sse(event: TaskEvent) -> str` emits `id`, `event`, and one JSON `data` line ending in a blank line; data is produced from `TaskProgressEventResponse` after `project_event` and public redaction.
- `iter_task_event_stream(*, persistence: TaskPersistenceService, task_id: UUID, user_id: UUID, cursor: int, poll_interval: float = 0.25, heartbeat_interval: float = 15.0) -> Iterator[str]` replays ordered history, polls after the last sequence, sends comments when idle, and stops after a failed/completed event.
- `GET /api/analysis-tasks/{task_id}/events/stream` returns `StreamingResponse` with `media_type="text/event-stream"`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`.

- [ ] **Step 1: Write failing SSE formatter/parser/route tests**

```python
# tests/api/test_progress_stream.py
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from data_analysis_agent.api.progress_stream import InvalidLastEventID, encode_sse, parse_last_event_id
from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.models import TaskEvent


def test_last_event_id_requires_a_positive_sequence():
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("17") == 17
    for value in ("", "0", "-1", "not-a-number"):
        with pytest.raises(InvalidLastEventID):
            parse_last_event_id(value)


def test_sse_encoder_contains_id_event_and_public_data():
    progress_event = TaskEvent(
        event_id=uuid4(),
        task_id=uuid4(),
        event_type=TaskEventType.TASK_CREATED,
        to_status=TaskStatus.PENDING,
        occurred_at=datetime.now(timezone.utc),
        sequence=3,
        progress=0,
        metadata={"file_path": "private", "attempt": 1},
    )
    payload = encode_sse(progress_event)
    lines = payload.splitlines()
    assert lines[0] == f"id: {progress_event.sequence}"
    assert lines[1] == f"event: {progress_event.event_type.value}"
    data = json.loads(lines[2].removeprefix("data: "))
    assert data["event_id"] == str(progress_event.event_id)
    assert data["sequence"] == progress_event.sequence
    assert "file_path" not in json.dumps(data)
    assert payload.endswith("\n\n")
```

```python
# tests/api/test_tasks.py
def test_openapi_and_sse_stream_path_are_exposed(task_api):
    _services, _broker, owner, _stranger, _ = task_api
    paths = owner.get("/openapi.json").json()["paths"]
    assert "/api/analysis-tasks/{task_id}/events/stream" in paths
```

- [ ] **Step 2: Run the new SSE tests and verify they fail**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/api/test_progress_stream.py tests/api/test_tasks.py::test_openapi_and_sse_stream_path_are_exposed
```

Expected: import/route failures because the stream module and endpoint do not exist.

- [ ] **Step 3: Implement the stream formatter and polling generator**

`parse_last_event_id` must reject whitespace-only, zero, negative, and non-integer values without echoing the supplied value in an error message. `encode_sse` must serialize only the exact public event fields and use `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` so every data payload occupies one line.

The generator must:

1. Call `list_events_after_for_user` with the current cursor and emit each result in ascending sequence order.
2. Advance the cursor only after yielding the event, so a client disconnect can safely reconnect from the last delivered ID.
3. Check the event type/status for `TASK_FAILED` or `TASK_COMPLETED` and return after that event.
4. When no event is available, check the current owner-scoped task status, emit `: keep-alive\\n\\n` at the heartbeat deadline, then sleep only for the bounded poll interval.
5. Stop promptly when the generator is closed; do not mutate task state.

Use a synchronous generator because the existing persistence layer is synchronous and Starlette runs synchronous streaming iterators in its worker context. Keep the poll and heartbeat intervals injectable for deterministic unit tests.

- [ ] **Step 4: Add the route and remove the broker dependency from history reads**

Add a `_persistence(request)` helper in `tasks.py` that checks only `application.task_persistence`. Keep `_services` for submit/cancel/retry routes that require a broker. Change the existing list-events route to use `_persistence` so development without Redis can inspect history.

Add the stream route before the generic task detail route. Validate `Last-Event-ID` before constructing `StreamingResponse`; verify `get_task_for_user` and return `TASK_NOT_FOUND` for missing/foreign tasks. Catch `PersistenceError` during the initial authorization query using the existing `TASK_PERSISTENCE_FAILURE` API error. Do not attempt to return JSON after streaming has started.

Export `TaskProgressEventResponse`, `InvalidLastEventID`, or only the public schema symbols required by the existing package export convention. Add the path assertion to `tests/api/test_app.py`.

- [ ] **Step 5: Run API SSE and compatibility tests**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/api/test_progress_stream.py tests/api/test_tasks.py tests/api/test_app.py tests/api/test_schemas.py
```

Expected: tests cover history replay, a completed task closing after the terminal event, a failed task exposing only safe code/message, `Last-Event-ID` replay without duplicates, owner isolation, headers, no-broker history access, and old paginated event responses.

- [ ] **Step 6: Commit the SSE API**

```powershell
git add src/data_analysis_agent/api/progress_stream.py src/data_analysis_agent/api/schemas.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/__init__.py tests/api/test_progress_stream.py tests/api/test_tasks.py tests/api/test_app.py
git commit -m "feat: add resumable task progress SSE"
```

### Task 6: Document usage and complete M14 verification

**Files:**
- Modify: `README.md` in the M12/M13 API documentation area
- Modify: `tests/integration/test_database_lifecycle.py` to assert event sequence survives engine/session restart
- Create: `tests/integration/test_progress_stream_lifecycle.py`
- Create: `sdd/m14-progress.md`

**Interfaces:**
- README documents `GET /api/analysis-tasks/{task_id}/events/stream`, `Last-Event-ID`, standard SSE `event`/`data` fields, owner authorization, and the fact that event payloads contain safe summaries only.
- `sdd/m14-progress.md` records the actual commit IDs, focused test counts, full test result, migration result, and any pre-existing environment skip.

- [ ] **Step 1: Write documentation/integration assertions before edits**

```python
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.api.application import APIApplication
from data_analysis_agent.config.settings import load_settings

from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.persistence.database import init_database
from data_analysis_agent.worker.broker import InMemoryTaskBroker


@pytest.fixture
def progress_api(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'progress.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
    )
    services = APIApplication.from_settings(settings)
    init_database(services.database.engine)
    services.configure_task_services(InMemoryTaskBroker())
    client = TestClient(
        create_app(services),
        headers={"X-User-ID": str(uuid4())},
    )
    try:
        yield services, client
    finally:
        services.database.engine.dispose()


def test_persisted_progress_history_is_replayable_after_task_completion(progress_api):
    services, owner = progress_api
    task_id = UUID(
        owner.post(
            "/api/analysis-tasks",
            json={"query": "分析", "idempotency_key": "stream-lifecycle"},
        ).json()["task_id"]
    )
    services.task_persistence.claim_task(task_id)
    services.task_persistence.transition_task(
        task_id=task_id, target=TaskStatus.EXPLORING, message="exploring"
    )
    services.task_persistence.fail_task(
        task_id, code="SAFE_FAILURE", message="safe failure"
    )

    response = owner.get(f"/api/analysis-tasks/{task_id}/events/stream")

    assert response.status_code == 200
    assert "task.failed" in response.text
    assert "SAFE_FAILURE" in response.text
    assert "api_key" not in response.text
```

- [ ] **Step 2: Run the integration test to confirm the documentation-facing contract is not yet recorded**

```powershell
E:\anaconda\Scripts\pytest.exe -q tests/integration/test_progress_stream_lifecycle.py
```

Expected: FAIL because the stream route or persisted M14 projection is not wired yet; the failing assertion must identify the missing route/event rather than a fixture import error.

- [ ] **Step 3: Add concise README and M14 progress records**

Document a JavaScript `EventSource` example that reads `event`, parses `data.sequence`, renders `stage`/`progress`, and relies on the browser's automatic `Last-Event-ID` reconnect behavior. State that the existing paginated endpoint is the history fallback and that artifact content remains separately authorized.

Update the existing database restart test to assert that restored task events have contiguous sequences beginning at 1 and can be queried with `list_after` after a new session is opened.

Update `sdd/m14-progress.md` with implementation commits, verification commands and observed results. Do not rewrite unrelated M09/M13 history files.

- [ ] **Step 4: Run the complete verification matrix**

```powershell
E:\anaconda\Scripts\pytest.exe -q
E:\anaconda\python.exe -m compileall -q src
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-final-check.sqlite upgrade head
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-final-check.sqlite downgrade 20260927_0005
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///m14-final-check.sqlite upgrade head
git diff --check
```

Expected: the full suite passes except any already documented environment-only skip; compileall and diff check exit with code 0; Alembic upgrade/downgrade/upgrade succeeds. Inspect the final event stream tests manually for strict sequence order, terminal closure, and absence of secret/code/raw-data strings.

- [ ] **Step 5: Review the full diff and commit documentation/verification records**

```powershell
git diff --stat HEAD~5..HEAD
git diff --check HEAD~5..HEAD
git status --short --branch
git add README.md sdd/m14-progress.md tests/integration/test_progress_stream_lifecycle.py tests/integration/test_database_lifecycle.py
git commit -m "docs: record M14 progress streaming verification"
```

Expected: only M14 source/tests/migration/docs appear in the diff; the pre-existing untracked `worktrees/` directory remains untouched and is not staged.

## Plan Self-Review

- Spec coverage: persistence, stable ordering, SSE replay, `Last-Event-ID`, terminal closure, failure display, ownership, code/raw-data permissions, API-key protection, legacy REST compatibility, migrations, Agent/Worker event sources, and verification are covered by Tasks 1-6.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation step is required; all commands and public method names are fixed above.
- Type consistency: `TaskEvent.sequence/stage/progress` flows through `TaskEventRecord`, `TaskEventORM`, repository methods, `TaskPersistenceService`, `TaskProgressEventResponse`, and `encode_sse`; `ProgressEventCallback` flows from `DataAnalysisAgent`/`AgentOrchestrator` to `AnalysisTaskWorker._on_agent_event`.
- Scope: no frontend, WebSocket, broker, or unrelated refactor is included.
