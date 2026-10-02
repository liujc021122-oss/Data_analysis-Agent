# M20 Progress

## 2026-10-01

- Read and applied the brainstorming, planning-with-files, using-git-worktrees, TDD, writing-plans, and verification-before-completion workflows.
- Created isolated branch `codex/m20-deployment` in `.worktrees/m20-deployment`.
- Created an independent Python environment and installed `.[dev,api,worker]`.
- Ran the full baseline suite: `944 passed, 1 skipped, 1 failed`; the failure is the existing SQLAlchemy 2.1.1 enum-boundary incompatibility described in `m20-findings.md`.
- Confirmed the main branch has no Docker/Compose/CI/reverse-proxy files; M15 frontend and M18 observability are separate worktrees.
- Confirmed local database direction: MySQL Community Server 8.0.44 is installed, `MySQL80` is running, and port 3306 is listening; PostgreSQL is out of scope for M20.
- Confirmed Docker CLI is not installed locally; Docker artifacts will be prepared but runtime verification will be limited to static/configuration checks on this machine.
- Updated M20 findings to remove PostgreSQL as an implementation target and defer enterprise PostgreSQL design.
- Confirmed M20 will integrate the existing M15 frontend source into the deployment branch while excluding unrelated M15 configuration/documentation changes.
- Approved the recommended unified local Compose topology: frontend, backend, worker, MySQL, Redis, MinIO, reverse proxy, and one-shot migrations.
- Approved the service topology and startup/data flow: persistent MySQL/Redis/MinIO volumes, one-shot Alembic migration gate, and local Compose using `STORAGE_BACKEND=s3` with `EXECUTION_BACKEND=local`.
- Approved configuration, secret handling, health probes, service health checks, and stdout JSON logging with local rotation.
- Approved Nginx routing, optional local HTTPS on ports 8080/8443, ignored certificate mounts, CI test/type-check/image-build stages, and runtime-only secret injection.
- Approved failure handling, persistence verification, and the layered test matrix.
- Wrote and self-reviewed `docs/superpowers/specs/2026-10-01-m20-docker-deployment-design.md`; committed it as `4b06a93`.
- Waiting for the user to review the written design before invoking the implementation-plan workflow.

## 2026-10-02

- Wrote and self-reviewed `docs/superpowers/plans/2026-10-02-m20-docker-deployment.md`; corrected the backend Dockerfile plan so source files are copied before normal package installation.
- Completed Task 1: constrained SQLAlchemy below 2.1, pinned mypy in the Python 3.12 constraints snapshot, imported the approved M15 frontend, and preserved the historical M06 report file.
- Task 1 commits: `b798b76`, `6814d05`, `01ca300`; task review passed after the mypy constraint and report-path cleanup fixes.
- Completed Task 2: added explicit local/S3 storage selection, runtime signing-secret injection, and sanitized local/S3 health checks.
- Task 2 commit: `9bbe20c`; focused settings/storage tests, storage regression tests, and targeted mypy passed; task review passed.
- Completed Task 3: added bounded JSON stdout logging and unauthenticated live/readiness API probes with safe dependency status mapping.
- Task 3 commit: `323c2b2`; 42 focused tests and targeted mypy passed; task review passed.
- Completed Task 4: added bounded Worker database/Redis health checks, `--healthcheck`, and MySQL connection timeouts while preserving normal Celery behavior.
- Task 4 commit: `f2a4f26`; 16 focused tests, 22 worker tests, and targeted mypy passed; task review passed.
- Completed Task 5: added constrained non-root backend/worker/migration image and multi-stage frontend/Nginx image with static health endpoint.
- Task 5 commit: `b583e94`; deployment contract tests and frontend type/test/build checks passed; task review passed.
- Completed Task 6: added the nine-service local Compose topology, MySQL/Redis/MinIO persistence, idempotent bucket initialization, migration gate, runtime environment template, and bounded service checks.
- Task 6 commit: `04921c9`; 10 deployment contract tests passed; Docker runtime unavailable locally; task review passed.
- Completed Task 7: added HTTP reverse proxy routing, optional local HTTPS override, ignored certificate mounts, and force-protected certificate helpers; preserved forwarded request IDs.
- Task 7 commits: `8defeac`, `8493968`; 12 deployment contract tests and ignore checks passed; task review passed after request-ID forwarding fix.
- Completed Task 8: added GitHub Actions jobs for constrained backend/frontend verification, Compose/migration integration, image builds, and conditional registry publishing.
- Task 8 commit: `8f55536`; 15 contract tests, targeted mypy, and frontend checks passed; Docker/actionlint unavailable locally; task review passed.

- Completed Task 9: documented local Compose prerequisites, secret replacement, startup, configuration validation, HTTP health checks, direct readiness and migration inspection, restart persistence, destructive `down -v` cleanup, optional local HTTPS on port `8443`, self-signed certificate warnings, and the empty API-key health-only behavior.
- Added an M20 Docker Compose startup section to `README.md` while preserving the existing SQLite/M13 startup instructions.
- Task 9 verification: the focused deployment contract suite and required documentation search were run after the documentation changes; Docker runtime verification remains unavailable locally because Docker is not installed.
