# M20 Docker Deployment and CI/CD Design

## Goal

Make the existing data analysis agent runnable on a new local machine through a reproducible Docker Compose topology, with MySQL persistence, Redis-backed worker execution, MinIO object storage, migrations, health checks, logs, local HTTPS, and CI verification.

## Scope Decisions

- M20 is local-machine-first. The primary deliverable is a development Compose stack that can be started from documented commands.
- The database service is MySQL 8.0. The current project already uses SQLAlchemy, Alembic, and PyMySQL, so the database framework remains unchanged.
- The M15 React/Vite frontend is included in this branch. Unrelated M15 environment and README edits are not imported.
- The M18 persistent observability module is out of scope. M20 adds safe stdout logging and container log rotation only.
- Local Compose uses `APP_ENV=development`, `EXECUTION_BACKEND=local`, and an explicit `STORAGE_BACKEND=s3` value so the stack can exercise MinIO without requiring nested Docker execution.
- Production-only enterprise operations, managed certificates, multi-node orchestration, and future database portability work are deferred.

## Architecture

The local topology contains these services:

| Service | Responsibility | Image/source |
| --- | --- | --- |
| `frontend` | Build and serve the M15 static application | Multi-stage Node build with Nginx runtime |
| `backend` | Serve the FastAPI application | Project Python image |
| `worker` | Execute queued analysis tasks through Celery | Same project Python image |
| `migrate` | Apply Alembic migrations before application startup | Same project Python image |
| `mysql` | Persist users, tasks, events, datasets, and artifacts metadata | MySQL 8.0 |
| `redis` | Celery broker and result backend | Redis 7 with AOF |
| `minio` | S3-compatible dataset and artifact object storage | MinIO |
| `reverse-proxy` | Serve the UI, proxy `/api`, and provide optional HTTPS | Nginx |

All services share a private Compose network. The API and worker connect to `mysql:3306`, `redis:6379`, and `minio:9000`; these internal addresses are not replaced with host-loopback addresses. The host maps MySQL to `3307:3306`, HTTP to `8080:80`, and optional HTTPS to `8443:443` so the stack does not conflict with the installed local MySQL service or common host web ports.

The backend and worker use one Dockerfile and one installed project artifact. Their commands differ only at runtime. The frontend uses the M15 source and produces a static `dist` directory. The reverse proxy is separate from the frontend runtime so routing and TLS can be changed without rebuilding the frontend assets.

## Startup and Data Flow

1. MySQL, Redis, and MinIO start with named volumes and native health checks.
2. A MinIO initialization command creates the configured bucket idempotently.
3. `migrate` waits for MySQL health and runs `alembic upgrade head` using `DATABASE_URL`.
4. `backend` and `worker` wait for successful migration completion and their required service health checks.
5. The backend exposes live/readiness probes; the worker starts Celery with the existing late-acknowledgement and stale-task recovery behavior.
6. `frontend` serves the compiled SPA and `reverse-proxy` routes browser requests to it and API requests to the backend.
7. A task request persists metadata in MySQL, publishes only the task ID through Redis, and stores datasets/reports in MinIO through the existing storage boundary.

If migration exits unsuccessfully, backend and worker remain stopped. Restarting the stack reruns the idempotent migration command. `docker compose down` preserves named volumes; data cleanup requires the explicit `down -v` command documented as destructive.

## Configuration and Secrets

Add an explicit `STORAGE_BACKEND` setting with `local` and `s3` values. Development and tests default to `local` when the setting is absent. The local Compose environment sets it to `s3` and supplies MinIO connection details. Production continues to require object storage configuration through the existing production validation.

Provide a committed `.env.compose.example` containing names and safe placeholders only. The real `.env.compose` file is ignored. Required runtime values are injected through Compose environment expansion:

- `OPENAI_API_KEY`
- `MYSQL_ROOT_PASSWORD`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MINIO_ROOT_USER`
- `MINIO_ROOT_PASSWORD`
- `STORAGE_SIGNING_SECRET`

The API key may be empty for startup and health-only checks; analysis calls must fail with the existing configuration error until a key is supplied. No secret is passed through a Dockerfile `ARG`, copied into an image layer, or written to a committed file. CI uses a non-production dummy key for offline tests and GitHub Secrets only for any registry push.

The backend and worker receive the same database, Redis, MinIO, storage signing, and LLM settings. The frontend receives only public build-time values such as its relative `/api` base path; it never receives the LLM key or storage credentials.

## Health Checks

Add unauthenticated, non-secret API probes:

- `GET /health/live` returns `200` when the process can serve requests.
- `GET /health/ready` returns `200` only when MySQL, Redis, and MinIO are reachable; dependency failure returns `503` with bounded component status and no credentials or exception text.

Compose checks use the service-native probes:

- MySQL: `mysqladmin ping` with the configured credentials;
- Redis: `redis-cli ping`;
- MinIO: `/minio/health/ready`;
- frontend and reverse proxy: a static `/healthz` response;
- migrate: process exit code `0`;
- backend: an HTTP request to `/health/ready`;
- worker: a project health command that verifies its configured Redis and database connections.

Health checks use bounded timeouts and retries. A live but dependency-disconnected API is therefore distinguishable from a stopped process.

## Logging

Application logs are written only to stdout/stderr. The formatter emits one JSON object per line with timestamp, level, logger, message, and available request/task identifiers. Existing exception sanitization remains the boundary for error text, and API keys, passwords, database URLs, storage signing values, source code, and host paths are never logged.

Compose configures local `json-file` rotation for application services with bounded size and file count. The log stream remains accessible through `docker compose logs backend worker` and does not depend on M18 database observability.

## Reverse Proxy and HTTPS

The base reverse proxy serves HTTP on host port `8080`:

- `/` proxies or serves the frontend runtime;
- `/api/` proxies to `backend:8000`;
- `/healthz` returns a proxy health response;
- forwarded host, protocol, client IP, and request ID headers are preserved safely.

An HTTPS Compose override adds host port `8443`, mounts `deploy/certs` read-only, and uses a separate Nginx configuration with certificate and key paths. PowerShell and Bash helper scripts generate local self-signed certificates. Certificate and key files are ignored by Git. The base HTTP stack remains usable without certificates; the HTTPS override fails clearly when the required files are absent.

## CI/CD

Add a GitHub Actions workflow triggered by pull requests and pushes:

1. Set up Python 3.12 and install the project with API, worker, and development extras under the repository dependency constraints.
2. Run backend pytest tests and Python type checking.
3. Run `npm ci`, frontend type checking, and Vitest in `frontend/`.
4. Run `alembic check`/migration validation against a CI MySQL service or Compose-managed MySQL.
5. Validate base and HTTPS Compose configuration and Nginx configuration.
6. Build backend and frontend images without using any secret as a build argument.
7. Start MySQL, Redis, and MinIO in a CI integration job, run migrations, and verify API readiness and worker connectivity.

Pull requests build and test but do not publish images. A main-branch publish step may push tagged images only when registry credentials are present as GitHub Secrets. CI test data uses fake LLM providers and never calls a real API.

Dependency reproducibility is part of CI. The project will constrain SQLAlchemy to the version range proven by the existing database tests and maintain a generated lock/constraints file for the Python 3.12 image and CI installation. This prevents the fresh-environment SQLAlchemy 2.1 enum-autoflush failure from silently changing the deployment result.

## Testing and Verification

Use test-first cycles for new Python behavior:

- settings tests cover `STORAGE_BACKEND` defaults, S3 selection, invalid values, and secret redaction;
- health tests cover live success, ready success, and each dependency failure mapping to `503`;
- worker health tests cover a ready connection and a bounded failure;
- migration tests cover the configured MySQL URL and idempotent upgrade entry point;
- deployment tests cover environment templates, ignored secret paths, and required Compose service names;
- frontend uses the existing M15 unit/component tests and type check;
- CI executes the complete backend suite and frontend suite.

On a machine with Docker, final verification is:

```text
docker compose config
docker compose build
docker compose up -d
curl http://localhost:8080/health/ready
docker compose exec migrate alembic current
docker compose down
docker compose up -d
```

The second startup verifies that MySQL, Redis, MinIO data, and migrations survive service restart. On the current machine Docker commands are reported as unverified because Docker is not installed; Python tests, frontend checks, static configuration checks, and authenticated local MySQL checks remain runnable.

## Acceptance Mapping

| Acceptance requirement | Design evidence |
| --- | --- |
| New machine can start from documentation | `.env.compose.example`, Compose file, startup guide, and idempotent migration/bucket initialization |
| Backend, worker, database communicate | Internal Compose DNS, dependency health checks, readiness endpoint, and CI integration job |
| Restart does not lose data | MySQL/Redis/MinIO named volumes, Redis AOF, restart verification |
| CI runs tests | Python pytest, frontend Vitest, type checks, migration and Compose validation |
| No API key hard-coded in production | Runtime-only environment injection, Docker ignore rules, CI secret boundary |
| Health identifies service failures | Live/readiness probes plus native MySQL/Redis/MinIO/frontend/worker checks |

## Non-Goals

- PostgreSQL or managed enterprise database deployment;
- Kubernetes, service mesh, multi-host scheduling, or automatic horizontal scaling;
- automatic public certificate issuance and renewal;
- Docker-in-Docker execution for the local development stack;
- persistent observability dashboards or cost analytics from M18.
