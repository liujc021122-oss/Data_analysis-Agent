# M20 Findings

## Repository Context

- The actual Git repository is the nested `data_analysis_agent-main` directory; the outer directory is not a Git repository.
- The worktree is `codex/m20-deployment` at `.worktrees/m20-deployment`.
- The root `task_plan.md`, `findings.md`, and `progress.md` are historical M09 planning files and must not be overwritten.
- The repository already has `.env.*.example` templates, `alembic/`, FastAPI API code, Celery worker code, S3/MinIO-compatible storage code, and optional Redis/Celery dependencies.
- There are no Dockerfiles, Compose files, reverse-proxy configuration, GitHub Actions workflow, or frontend directory on the main branch.

## Existing Module Boundaries

- API entry point: `data_analysis_agent.api.app:create_app`.
- Settings entry point: `data_analysis_agent.config.settings:load_settings`.
- Database migrations: Alembic with `alembic/env.py` and version files.
- Worker entry point: `data_analysis_agent.worker.celery_app:build_celery_app` and `data_analysis_agent.worker.cli`.
- Storage factory: `data_analysis_agent.storage.factory:build_storage`; the S3 implementation is MinIO-compatible.
- M15 frontend exists on the separate `codex/m15-frontend` worktree and is not present on `main`.
- M18 observability exists on the separate `codex/m18-observability` worktree and is not present on `main`.
- The requested database service is resolved to MySQL: `load_settings()` and `alembic/env.py` already require a MySQL backend, and `pyproject.toml` already declares `PyMySQL`.
- M15 provides a Vite/React frontend with `npm run build`, `npm run typecheck`, and `npm run test`; its API client uses `/api` by default and is suitable for a reverse-proxy-served static image.
- M18 provides a JSON log formatter and observability persistence/routes, but those changes are not in the M20 starting branch.
- Local machine check: MySQL Community Server 8.0.44 is installed at `E:\MySQL\MySQL Server 8.0\bin\mysql.exe`; the `MySQL80` Windows service is running and TCP port 3306 is listening.
- `mysqladmin ping` reached the server but was rejected because the Windows default client user is `ODBC`; no database credentials were read or exposed. Authenticated database verification requires credentials supplied by the user/environment.
- Docker CLI is not installed on this machine, so Docker image/Compose execution must be validated structurally or on a machine with Docker; local service verification should use the installed MySQL server and non-conflicting ports.

## Verification Baseline

- Isolated environment installed with `.[dev,api,worker]`.
- Full baseline: `944 passed, 1 skipped, 1 failed`.
- The single failure is `tests/database/test_core_repositories.py::test_malformed_enum_row_is_mapped_at_boundary`.
- Failure occurs because the unconstrained fresh environment resolved SQLAlchemy 2.1.1, which rejects the test's intentionally malformed enum value during autoflush before the repository mapping boundary.
- This is evidence that deployment must make dependency resolution reproducible; the failure is not caused by M20 changes.

## Resolved Design Constraints

- The M20 database service and connection examples use MySQL 8.0; PostgreSQL is explicitly out of scope for this module.
- The scope is local-machine runnable first. Enterprise PostgreSQL portability, managed database operations, and production certificate automation are future work.

## Remaining Design Questions

- M20 scope decision: integrate the M15 frontend source into this branch; do not import unrelated M15 environment or README edits unless required by the deployment contract.
- Whether the existing M18 observability work is in scope for this M20 branch or only stdout logging/health checks should be wired now.

## Task 9 Verification Finding

- Docker and Docker Compose are the only unverified runtime prerequisite on the current development machine. The deployment guide therefore records structural contract checks as local evidence and reserves image builds, container startup, network routing, migrations, HTTPS, and runtime health responses for CI or a machine with Docker Desktop.
- The documented local Compose configuration uses host MySQL port `3307` to avoid the installed MySQL service on port `3306`. `down` preserves named volumes; `down -v` removes MySQL, Redis, and MinIO data.

## Task 10 Verification Findings (2026-10-02)

- The focused M20 Python suite passes (62 tests), and the constrained full suite runs with Python 3.12.7 and SQLAlchemy 2.0.54. Its exact result is `979 passed, 1 skipped, 2 failed`; the earlier SQLAlchemy 2.1.1 enum-autoflush failure is absent.
- The two initial full-suite failures were `tests/test_cli_dataset_ids.py::test_build_dataset_resolver_rejects_production_local_storage` and `tests/test_cli_dataset_ids.py::test_production_cli_dataset_id_fails_closed_before_database_creation`. Their `make_settings()` fixture lacked the new storage fields, so `build_storage()` raised `AttributeError` before the expected `ConfigurationError`. Commit `506384f` fixed the compatibility boundary with safe legacy defaults and regression tests; the final full suite is green.
- Frontend installation, typecheck, 30 tests across 12 files, and build pass. `npm ci` reports four moderate dependency advisories; no dependency update was made during verification. `frontend/dist` is ignored, while TypeScript emitted an untracked `frontend/tsconfig.tsbuildinfo` locally; that generated file was removed after verification.
- The exact requested `git grep` pattern reports Compose variable references (`${...}`), a README `your_api_key_here` placeholder, and synthetic test strings. Inspection found no real-looking committed API key, database password, MinIO credential, or signing secret. `.dockerignore` excludes `.env*`, `.env.compose`, certificates, and build output; Git tracks only environment examples and the certificate directory placeholder.
- `docker --version` fails because the command is absent. The CI workflow contains base/HTTPS Compose config, integration/migration, and image-build jobs, but their execution was not observed in this task. Local Docker/Compose runtime behavior remains unverified. This supersedes the earlier Task 9 statement that Docker was the only remaining verification gap: the newly observed backend regression is an additional blocker to M20 closure.
- After `506384f`, the final constrained backend suite reports `984 passed, 1 skipped`, and the final M20-focused suite reports `74 passed`. Docker/Compose runtime behavior remains the only unverified boundary on this machine.

## Task 10 follow-up verification (2026-10-02)

- The authentication audit test was reproduced independently: consecutive `AuditWriter` calls can receive the same Windows wall-clock timestamp, and the test's `occurred_at, event_id` ordering then uses a random UUID tie-breaker. This produced `REGISTERED, LOGIN_SUCCEEDED, LOGGED_OUT, LOGIN_FAILED` even though the calls were made in the opposite order.
- The TDD regression test in `tests/services/test_auth_service.py` failed with a frozen clock before the fix. `AuditWriter` now serializes timestamp allocation with a lock and advances equal or backward candidates by one microsecond, preserving write order within one writer instance without changing the event schema.
- The regression test passed after the fix, the full authentication service file passed five consecutive times, and the fresh full backend suite reports `988 passed, 1 skipped`.
- The latest focused deployment/configuration/health suite reports `68 passed`; targeted mypy reports no issues in the four CI-listed modules. Frontend install, typecheck, 30 tests, and production build pass.
- `git diff --check` passes. The requested secret scan reports only `${...}` runtime references and synthetic test strings; no real-looking credential was found. Docker CLI is still unavailable locally, so Docker/Compose runtime behavior remains unverified here and is covered by the CI workflow.

## Final verification addendum (2026-10-02)

- The audit timestamp precision change is isolated to `AuditEventORM.occurred_at`: MySQL uses `DATETIME(6)` through `UTCDateTimeMicrosecond`, while the existing portable `UTCDateTime` remains unchanged for other columns and dialects.
- The first cache-safety assertion passed through inherited lookup but SQLAlchemy still emitted a warning; inspecting the subclass dictionary reproduced the issue, the strengthened regression test failed, and an explicit subclass `cache_ok = True` made the test pass without warnings.
- The fresh full backend result is `992 passed, 1 skipped`. CI-targeted mypy, frontend typecheck/tests/build, the 15-test deployment/packaging suite, and `git diff --check` all pass.
- Docker remains the only unverified runtime boundary on this host because the `docker` command is unavailable. No claim is made here about container communication, persistent volumes, live migrations, proxy routing, HTTPS, or image builds beyond the checked-in CI definitions.
