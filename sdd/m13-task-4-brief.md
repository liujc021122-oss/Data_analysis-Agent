## Task 4: Task queries, events, cancellation, and manual retry

**Files:**
- Test: `tests/api/test_tasks.py`
- Modify: `src/data_analysis_agent/domain/state.py`, `src/data_analysis_agent/persistence/orm_models.py`, `src/data_analysis_agent/services/persistence.py`, `src/data_analysis_agent/worker/service.py`
- Create: `alembic/versions/20260927_0005_api_manual_retry.py`
- Modify: `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/application.py`
- Modify: `tests/api/conftest.py`, `src/data_analysis_agent/api/routers/tasks.py`

**Interfaces:**
- `TaskPersistenceService.get_task_for_user(task_id, user_id) -> AnalysisTask | None`.
- `TaskPersistenceService.list_tasks_for_user(user_id, status, offset, limit) -> tuple[list[AnalysisTask], int]`.
- `TaskPersistenceService.list_events_for_user(task_id, user_id, offset, limit) -> tuple[list[TaskEvent], int]`.
- `TaskPersistenceService.retry_failed_task(task_id, user_id) -> AnalysisTask`.
- `TaskSubmissionService.cancel_for_user(task_id, user_id)` and `retry_for_user(task_id, user_id)`.

- [ ] **Step 1: Add failing state, persistence, and HTTP tests.**

```python
def test_failed_task_can_transition_to_queued():
    assert can_transition(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_task_api_creates_once_and_lists_owner_status(api_client, broker):
    payload = {"query": "分析销售", "idempotency_key": "api-key"}
    first = api_client.post("/api/analysis-tasks", json=payload)
    second = api_client.post("/api/analysis-tasks", json=payload)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert second.json()["created"] is False
    assert len(broker.messages) == 1


def test_cross_user_task_is_not_revealed(api_client, other_user_client, task_id):
    response = other_user_client.get(f"/api/analysis-tasks/{task_id}")
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_failed_task_retry_enqueues_once(api_client, broker, failed_task):
    response = api_client.post(f"/api/analysis-tasks/{failed_task}/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert broker.messages[-1].task_id == failed_task
```

- [ ] **Step 2: Run the task tests and verify red.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_tasks.py tests/domain/test_state.py -q
```

Expected: FAIL because the transition, owner-scoped query methods, retry transaction, and task Router are absent.

- [ ] **Step 3: Add the legal retry transition and migration.**

Add `TaskStatus.QUEUED` to `LEGAL_STATUS_TRANSITIONS[TaskStatus.FAILED]` and to the ORM check constraint. Create migration `20260927_0005_api_manual_retry.py` that drops and recreates `ck_task_events_legal_status_transition`, preserving all M12 transitions and adding:

```sql
(from_status = 'FAILED' AND to_status = 'QUEUED')
```

The migration must support SQLite batch mode and MySQL upgrade/downgrade. Add an Alembic test that upgrades a fresh SQLite database to head and verifies the check constraint permits `FAILED -> QUEUED`.

- [ ] **Step 4: Implement owner-scoped task queries and retry transaction.**

Repository queries must join/filter `AnalysisTaskORM.user_id`, order by `created_at, task_id`, and return `(items, total)` after applying offset/limit. Event queries must first verify task ownership. `retry_failed_task` must lock the task, reject foreign/unknown IDs, allow only `FAILED`, clear `error_code/error_message`, transition to `QUEUED`, append one event, and commit atomically.

`TaskSubmissionService.retry_for_user(task_id, user_id) -> TaskSubmissionResult` must call the persistence method, enqueue exactly once with only the UUID, and if enqueue fails mark the task `FAILED` with `TASK_ENQUEUE_FAILED` before raising `TaskEnqueueError`. Existing M12 `cancel(task_id)` remains compatible; the API uses the owner-scoped method.

- [ ] **Step 5: Implement the task Router and response mapping.**

Add `AnalysisTaskListResponse`, `TaskRetryResponse`, and `TaskEventListResponse` using `PageResponse`. `POST /api/analysis-tasks` returns `202`; duplicate idempotent requests return the same task ID with `created=false` and no second broker message. `GET` endpoints return only owner records. Cancellation is idempotent for the owner; retry returns `409` for any non-`FAILED` status. Map persisted errors to stable nested `ErrorResponse` values without raw database text. Include Artifact summaries only through owner-scoped repository access.

- [ ] **Step 6: Run task, migration, Worker, and database regression tests.**

Run:

```powershell
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m pytest tests/api/test_tasks.py tests/worker tests/domain/test_state.py tests/database/test_task_lifecycle.py tests/database/test_schema.py -q
& 'E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe' -m alembic -x 'db_url=sqlite:///C:/Users/86136/AppData/Local/Temp/m13-api.sqlite' upgrade head
```

Expected: all selected tests pass and migration reaches `20260927_0005`.

- [ ] **Step 7: Commit the task API task.**

```powershell
git add src/data_analysis_agent/domain/state.py src/data_analysis_agent/persistence src/data_analysis_agent/services/persistence.py src/data_analysis_agent/worker/service.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/schemas.py alembic/versions/20260927_0005_api_manual_retry.py tests/api/test_tasks.py
git commit -m "feat: expose analysis task API"
```
