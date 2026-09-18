# M02 Findings

## Task 6

- `pyproject.toml` already contains the explicit runtime dependency `pydantic>=2.0,<3.0`.
- The worktree was clean at Task 5 commit `ae34fa8`.
- Existing SDD progress records the prior no-key baseline as 67 passed.
- The new cross-layer tests pass against the existing domain, persistence, and API implementations, so no production patch was needed.
- Complete no-key regression was 126 passed at Task 6, 127 passed after the first final hardening, and 162 passed after the complete domain JSON-boundary hardening; no Agent/Worker/API orchestration was added.
- Final domain hardening commits `da11b9f`, `22e2275`, `f66e235`, and `cc384f7` close pre-constructed FrozenDict, JSON type, UTC time, cycle/key, and stable-set-sorting boundaries.
- The host's `python` command is a WindowsApps alias and produced exit 9009 for the brief's external check. The explicit shared venv interpreter is a safe equivalent and passed after reinstalling this worktree editable with `pip install -e . --no-deps`.
- The external check used the explicitly named temporary directory `data-analysis-agent-m02-external`; it was not added to Git.

## M03 Final Review Fix

- `TaskRepository._to_domain` currently orders association rows by `dataset_id`; `compute_request_hash` intentionally hashes the request's tuple order.
- `TaskPersistenceService` already iterates the request tuple in order, but `attach_dataset` has no position field and old callers rely on its two-argument signature.
- Existing association rows from revision 0001 have no meaningful persisted order, so revision 0003 must backfill deterministically by `(task_id, dataset_id)` before making position non-null and adding per-task position uniqueness.

## Task 3: encoding correctness

- Before the fix, a valid non-BOM UTF-8 Chinese CSV was classified as `gb18030`; the resulting preview headers and values were mojibake.
- The root cause was charset-normalizer running before strict UTF-8 decoding. Its best match can have low coherence on small valid samples.
- The regression test now requires `utf-8` and intact Chinese headers/values. The decoder order is BOM, strict UTF-8, scored charset-normalizer, strict `gb18030`, then strict `gbk`.
