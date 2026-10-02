# M20 Docker Deployment and CI/CD Plan

## Goal

Make the backend, worker, frontend, database, Redis, MinIO, and reverse proxy reproducibly deployable on a new machine, with migration, health, logging, HTTPS, and CI verification.

## Status

- [complete] Create isolated worktree and inspect repository context
- [complete] Establish dependency environment and record baseline
- [complete] Confirm architecture and deployment scope
- [complete] Review committed design document and write implementation plan
- [complete] Stabilize dependencies and import the M15 frontend
- [complete] Add explicit storage selection and safe runtime configuration
- [complete] Add JSON logging and API live/readiness probes
- [complete] Add Worker health command and bounded database connections
- [complete] Build backend and frontend images
- [complete] Define MySQL, Redis, MinIO, migration, and application Compose services
- [complete] Add Nginx routing and local HTTPS
- [complete] Add CI for tests, types, migrations, Compose, and images
- [complete] Document startup, restart, HTTPS, and verification
- [complete] Add container images and Compose environments
- [complete] Add configuration, migrations, health checks, and logging
- [complete] Add reverse proxy and HTTPS workflow
- [complete] Add CI test/type-check/image-build workflow
- [complete] Document startup, production secrets, and verification
- [complete] Run focused and full verification

## Constraints

- Preserve the main branch and existing M15/M18/M19 worktrees.
- Do not hard-code API keys or production secrets.
- Reuse the existing FastAPI, Celery, Alembic, SQLAlchemy, and S3-compatible storage boundaries.
- Keep CI offline with fake providers unless an integration job explicitly supplies services.
- Treat Docker, Compose, and CI configuration as testable artifacts.
- Use MySQL 8.0 for the local database service; PostgreSQL is out of scope for M20 and deferred to a future enterprise design task.
- Do not require Docker to be installed on the current development machine; validate Docker artifacts structurally and run application checks against the installed local MySQL where credentials permit.

## Errors Encountered

| Error | Attempt | Resolution |
| --- | ---: | --- |
| Native worktree tool reported `Not a git repository` | 1 | Switched to the repository-local Git worktree fallback after verifying `.worktrees/` is ignored. |
| Initial Windows Python launcher created no usable virtual environment | 1 | Used `E:\anaconda\python.exe` to create the isolated worktree `.venv`. |
| Baseline collection with the main environment lacked `argon2` | 1 | Installed the declared `.[dev,api,worker]` extras in the isolated environment. |
| Fresh dependency resolution produced one SQLAlchemy 2.1.1 baseline failure | 1 | Record as dependency reproducibility scope; do not alter unrelated production code before design approval. |
| Windows audit timestamps tied during consecutive authentication writes | 1 | Reproduced with a frozen clock; added a thread-safe monotonic timestamp allocator and regression test in `AuditWriter`. |
| MySQL audit timestamps needed fractional-second ordering | 1 | Added a dedicated `UTCDateTimeMicrosecond` type, `DATETIME(6)` migration, ORM mapping, and cache-safety regression coverage; kept the generic UTC type portable. |
