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
- Completed Task 9: documented new-machine Compose startup, readiness/migration/log inspection, restart persistence, destructive volume cleanup, optional HTTPS, secret behavior, and local Docker limitations; linked the guide from README.
- Task 9 commit: `b18e1b9`; 13 deployment contract tests and documentation searches passed; task review passed.

- Completed Task 9: documented local Compose prerequisites, secret replacement, startup, configuration validation, HTTP health checks, direct readiness and migration inspection, restart persistence, destructive `down -v` cleanup, optional local HTTPS on port `8443`, self-signed certificate warnings, and the empty API-key health-only behavior.
- Added an M20 Docker Compose startup section to `README.md` while preserving the existing SQLite/M13 startup instructions.
- Task 9 verification: the focused deployment contract suite and required documentation search were run after the documentation changes; Docker runtime verification remains unavailable locally because Docker is not installed.

## Task 10 verification (2026-10-02)

- Focused M20 Python suite: 62 passed. Deployment contract rerun: 13 passed.
- Full constrained Python 3.12.7 / SQLAlchemy 2.0.54 suite before the compatibility fix: 979 passed, 1 skipped, 2 failed. The prior SQLAlchemy 2.1.1 enum-autoflush failure did not recur. The failures were fixed by `506384f`; the final full backend suite is recorded below.
- Frontend: `npm ci`, typecheck, 30 Vitest tests, and production build passed. `frontend/dist` remains ignored. `npm ci` reported four moderate dependency advisories.
- Structural checks: deployment contract passed; `git diff --check` passed; the requested secret scan found only variable references, a README placeholder, and synthetic test values. No real-looking key was found. Docker CLI is absent, so Compose configuration, image builds, service health, migrations, routing, and restart persistence remain unverified locally; CI defines Docker-enabled checks but no CI result was available in this run.
- Reviewed the branch history, diff against `main`, Dockerfiles, Compose and HTTPS override, CI workflow, deployment guide, health interfaces, and secret/ignore boundaries. Task 10 records the evidence and the open backend regression without changing implementation files.
- Compatibility fix: `build_storage()` now supplies safe legacy-object defaults at its boundary while keeping explicit `Settings.storage_backend` authoritative. The regression tests and targeted mypy check pass.
- Investigated a non-deterministic authentication audit test failure. Windows `datetime.now()` can return the same timestamp for consecutive audit writes, while the test ordered ties by random UUID; the observed order was `REGISTERED, LOGIN_SUCCEEDED, LOGGED_OUT, LOGIN_FAILED`.
- Added a thread-safe monotonic timestamp allocator to `AuditWriter` and a regression test that freezes the clock. The regression test was observed failing before the implementation and passing afterward; the authentication service file passed in five consecutive runs.
- Final constrained backend suite after the audit fix: `988 passed, 1 skipped`.
- Latest deployment/configuration/health focused suite: `68 passed`; targeted mypy reports no issues in four files; frontend `npm ci`, typecheck, 30 tests, and production build pass; `git diff --check` passes.
- The required secret scan found only runtime variable references and synthetic test values. Docker CLI is unavailable locally, so Compose parsing, image builds, container health, migrations, proxy routing, HTTPS, and restart persistence remain CI-only verification boundaries.

## Final follow-up verification (2026-10-02)

- Added `UTCDateTimeMicrosecond` for MySQL audit-event timestamps and migration `20261002_0007`; general `UTCDateTime` remains the portable type used by other tables.
- Added an explicit `cache_ok` regression assertion after SQLAlchemy warned that the subclass did not expose a cache key; the implementation now declares the subclass cache-safe.
- Final constrained backend suite after the timestamp precision fix: `992 passed, 1 skipped`.
- CI-targeted mypy: no issues in four source files. Frontend: `npm ci`, typecheck, 30 tests, and production build passed; `npm ci` reported four moderate audit advisories.
- Deployment and dependency contract suite: `15 passed`; `git diff --check` passed. The credential-pattern scan found no API/private-key-shaped committed secret.
- Docker CLI is absent on this machine. Compose startup, image builds, container health, migration execution against MySQL, proxy routing, HTTPS, and restart persistence remain CI/Docker-host verification boundaries.
