# M13 Task 4 review: analysis task API

Scope: task submission, owner-scoped list/detail/events, cancellation, manual retry, and the `FAILED -> QUEUED` migration. Task 5 (artifact endpoints) is not included.

## Contracts checked

- Submission uses the persisted idempotency key and enqueues only the first creation.
- Task and event queries are scoped to the authenticated owner; foreign IDs return the same 404 shape as missing IDs.
- Cancellation is idempotent and revokes only when status changed.
- Retry is allowed only from `FAILED`; a conditional database update prevents a stale claim from enqueueing again.
- Enqueue failure restores `FAILED` and returns a stable 503 error without broker exception text.
- Detail artifacts use owner-scoped lookup and public DTO fields; local file paths are not returned.
- `20260927_0005` extends the event transition constraint on SQLite and MySQL.

## TDD evidence

- Initial state-contract RED: `FAILED -> QUEUED` was rejected.
- API setup RED: `APIApplication.configure_task_services` was absent (4 setup errors).
- Conditional-update RED: `TaskRepository.update_if_status` was absent.
- Request-ID RED: nested task error returned `task-status` instead of the response request ID.
- Focused GREEN: `tests/api/test_tasks.py tests/domain/test_state.py`: 22 passed before added edge cases; subsequent focused task and migration tests: 8 passed.
- Worker/database regression: 57 passed.
- Final full suite after the request-ID correction: 887 passed, 1 skipped (symlinks unavailable).
- Focused task and migration check after that correction: 9 passed. `git diff --check` exited 0.
- Fresh SQLite `alembic upgrade head` and a second upgrade both exited 0. The resulting revision is `20260927_0005`.

## Working tree note

The shared worktree contains pre-existing, uncommitted M12 changes, including the worker package and migration `0004`, mixed into files needed by this task. Preserve them when staging or integrating. This task was not committed separately to avoid committing those unrelated changes as part of Task 4.
