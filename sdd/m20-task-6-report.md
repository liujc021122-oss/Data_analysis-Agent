# M20 Task 6 Report: Persistent Compose Services

## Scope

Implemented Task 6 on the `m20-deployment` worktree from the M20 branch. The existing `sdd/m20-*` planning files and earlier task reports were preserved. Changes are limited to the Compose topology, its safe environment template, ignore rules, deployment contract tests, and this report.

## Changes

- Added `compose.yaml` with exactly the approved `frontend`, `backend`, `worker`, `migrate`, `mysql`, `redis`, `minio`, `minio-init`, and `reverse-proxy` services.
- Pinned MySQL, Redis, MinIO, MinIO Client, frontend proxy, and reverse proxy images. MySQL uses port `3307:3306`; the reverse proxy publishes `8080:80`; frontend, backend, and MinIO remain internal to the Compose network.
- Added `mysql_data`, `redis_data`, and `minio_data` named volumes. Redis uses AOF persistence, MySQL persists `/var/lib/mysql`, and MinIO persists `/data`.
- Added MySQL, Redis, MinIO, backend, worker, frontend, and reverse-proxy health checks with bounded intervals, timeouts, retries, and startup periods. The backend probe uses a Python standard-library request to `/health/ready`; the worker probe uses its existing `--healthcheck` command.
- Added startup gates so migration waits for MySQL readiness, and backend and worker wait for healthy infrastructure, successful bucket initialization, and successful migration.
- Made `minio-init` idempotent with `mc mb --ignore-existing`; its shell references use `$$` so Compose leaves them for container evaluation.
- Added runtime environment interpolation for database, Redis, MinIO, storage signing, execution, logging, and LLM settings. No secret is passed through a build argument.
- Added an inline reverse-proxy configuration that routes `/api/` and health requests to backend and all other requests to frontend.
- Added `.env.compose.example` with safe local placeholders and documentation that real deployments must replace every `change-me` value; changing `MYSQL_PASSWORD` also requires updating `DATABASE_URL`.
- Added ignore rules for `.env.compose`, deployment certificate keys, and frontend dependencies/build output.
- Added static deployment contract tests for service topology, startup gates, persistence, ports, secret injection, bounded probes, idempotent initialization, internal DNS, template values, and ignore rules.

## TDD evidence

The new Compose tests were added before the implementation files. The first focused run failed with the expected missing-file errors: six new contract tests failed because `compose.yaml` and `.env.compose.example` were absent. After implementation, the focused deployment suite passed.

## Verification

- `.\\.venv\\Scripts\\python.exe -m pytest tests/deployment/test_contract.py -q`: `8 passed`.
- `.\\.venv\\Scripts\\python.exe -m pytest tests/deployment/test_contract.py tests/packaging/test_dependency_constraints.py -q`: `10 passed`.
- YAML structural parsing succeeded for `compose.yaml`.
- `git diff --check` completed without whitespace errors.
- Docker and Docker Compose are unavailable locally. No image build, container startup, network routing, migration execution, or runtime health behavior was claimed or verified.

## Commit

The implementation is committed as `feat: add persistent local compose services`.
