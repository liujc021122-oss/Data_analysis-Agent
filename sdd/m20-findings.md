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
