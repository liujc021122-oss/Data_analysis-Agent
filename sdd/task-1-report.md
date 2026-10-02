# Task 1 Report: Stabilize Python Dependencies and Import the M15 Frontend

## Scope

Implemented Task 1 in `E:/桌面/data_analysis_agent-main/data_analysis_agent-main/.worktrees/m20-deployment`.

The change includes the dependency boundary, the Python 3.12 constraints snapshot, targeted mypy settings, the dependency regression test, and the approved tracked frontend tree from `codex/m15-frontend`. The pre-existing untracked M20 planning files were preserved.

## Files changed

- `pyproject.toml`
  - Changed SQLAlchemy to `SQLAlchemy>=2.0,<2.1`.
  - Added `mypy>=1.11,<2.0` to the `dev` extra.
- `requirements/constraints-py312.txt`
  - Added the Python 3.12 package snapshot from the M20 environment.
  - Excluded the local editable project and replaced the known incompatible SQLAlchemy 2.1.1 result with `SQLAlchemy<2.1`.
  - Contains no credentials or local paths.
- `mypy.ini`
  - Added the exact targeted Python 3.12 mypy configuration from the brief.
- `tests/packaging/test_dependency_constraints.py`
  - Added the two required dependency regression tests.
- `frontend/`
  - Imported the tracked M15 package manifest, lockfile, Vite/Vitest/TypeScript configuration, React source, tests, and public favicon.
  - Preserved the `@` Vite alias and the `/api` proxy default `http://127.0.0.1:8000`.
  - Excluded the M15 environment example and all generated or ignored output.

## TDD evidence

1. RED: ran

   `\.venv\Scripts\python.exe -m pytest tests/packaging/test_dependency_constraints.py -q`

   Result: 2 failed. The first test failed because `requirements/constraints-py312.txt` was absent; the second failed because the mypy extra was absent. This confirmed the test was exercising the requested missing dependency boundary.

2. GREEN: added the metadata, constraints, and mypy configuration, then reran the focused test.

   Result: `2 passed in 0.08s`.

## Verification

- `\.venv\Scripts\python.exe -m pip install -e ".[dev,api,worker]" --constraint requirements/constraints-py312.txt`
  - Passed. SQLAlchemy resolved to `2.0.54`; mypy resolved to `1.20.2`.
- `\.venv\Scripts\python.exe -m pytest tests/packaging/test_dependency_constraints.py -q`
  - Passed: 2 tests.
- `npm ci` in `frontend/`
  - Passed: 189 packages installed and audited.
- `npm run typecheck` in `frontend/`
  - Passed: `tsc -b --pretty false`.
- `npm run test` in `frontend/`
  - Passed: 12 test files and 30 tests.
- `npm run build` in `frontend/`
  - Passed: TypeScript build and Vite production build.
- `git diff --check`
  - Passed.

## Required mypy command

The exact targeted command from the brief was attempted:

`\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/config/settings.py src/data_analysis_agent/config/logging.py src/data_analysis_agent/api/health.py src/data_analysis_agent/worker/health.py`

It could not run because the M20 baseline does not yet contain `src/data_analysis_agent/config/logging.py`, `src/data_analysis_agent/api/health.py`, or `src/data_analysis_agent/worker/health.py`. Those files belong to later M20 tasks, so they were not created or changed in this task. The command reported the missing `config/logging.py` path before type checking.

## Self-review

- SQLAlchemy metadata and constraints prevent the known 2.1 enum regression.
- The constraints snapshot has no editable project entry, credentials, or local filesystem path.
- The frontend package scripts are exactly `build: tsc -b && vite build`, `typecheck: tsc -b --pretty false`, and `test: vitest run`.
- The imported frontend tree contains the M15 lockfile, source tests, Vite alias, API proxy, and public asset.
- No backend behavior or later-task deployment files were modified.
- The worktree also contains pre-existing untracked `sdd/m20-findings.md`, `sdd/m20-progress.md`, and `sdd/m20-task-plan.md`; they are outside this task and were not staged.

## Concerns

- The specified mypy gate remains unavailable until the three later-task health/logging modules are added.
- `npm ci` reported four moderate dependency audit findings in the imported M15 dependency graph. The brief requires preserving the approved lockfile, so the lockfile was not changed.

## Commit

To be recorded after final staging and verification:

`build: stabilize dependencies and add frontend workspace`
