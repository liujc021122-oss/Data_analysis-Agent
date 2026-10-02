# M20 Docker Deployment and CI/CD Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the existing MySQL-backed data analysis agent start reproducibly on a new local machine with Docker Compose, persistent services, migration gating, health checks, local HTTPS, safe logs, and automated CI validation.

**Architecture:** The backend and worker use one Python 3.12 image and receive different runtime commands. A one-shot Alembic migrate service must complete before the API and worker start. A React/Vite frontend is built into a static Nginx image; a separate reverse proxy routes / to the frontend and /api/ to the backend. MySQL, Redis, and MinIO use named volumes and native health checks.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, PyMySQL, Celery, Redis 7, MySQL 8.0, MinIO S3, React/Vite, Nginx, Docker Compose, GitHub Actions, pytest, Vitest, and targeted mypy.

## Global Constraints

- Use MySQL 8.0 for the local database service; PostgreSQL is out of scope for M20 and deferred to a future enterprise design task.
- Keep APP_ENV=development, EXECUTION_BACKEND=local, and STORAGE_BACKEND=s3 in local Compose.
- Use the Compose service names mysql, redis, and minio for container-to-container connections; never use localhost inside a container.
- Map host ports 3307:3306, 8080:80, and 8443:443.
- Persist MySQL, Redis, and MinIO data in named volumes; Redis must run with AOF enabled.
- Run migrations in a one-shot service and make backend and worker depend on successful migration completion.
- Inject API keys, database passwords, MinIO credentials, and storage signing secrets only at runtime. Never use Dockerfile ARG for secrets.
- Commit .env.compose.example only; ignore .env.compose, local certificates, keys, and generated output.
- Expose unauthenticated GET /health/live and GET /health/ready. Readiness returns 503 when MySQL, Redis, or MinIO is unavailable.
- Write application logs to stdout/stderr as one JSON object per line and configure bounded Compose log rotation.
- The base Compose stack is HTTP-only; the HTTPS override mounts locally generated certificates and listens on host port 8443.
- CI runs backend tests, frontend tests, targeted type checks, migration/configuration validation, and image builds without real LLM calls.
- Docker runtime verification is unavailable on the current machine because Docker is not installed; structural checks and non-Docker application tests must still run locally.
- Preserve the existing M15 frontend behavior and import only its frontend source/configuration needed by this deployment branch.
- Do not include M18 persistent observability, Kubernetes, managed certificates, multi-node orchestration, or PostgreSQL portability work.

## File Map

- pyproject.toml: constrain SQLAlchemy below 2.1 and add mypy to the development extras.
- requirements/constraints-py312.txt: record the tested Python 3.12 dependency constraints used by the image and CI.
- src/data_analysis_agent/config/settings.py: add STORAGE_BACKEND parsing, validation, and redaction coverage.
- src/data_analysis_agent/config/logging.py: add the stdout JSON formatter and safe record fields.
- src/data_analysis_agent/storage/factory.py: select local or S3 storage explicitly.
- src/data_analysis_agent/storage/__init__.py, local.py, and s3.py: expose and implement storage health checks.
- src/data_analysis_agent/persistence/database.py: use a bounded MySQL connection timeout.
- src/data_analysis_agent/api/health.py and api/app.py: implement and register live/readiness probes.
- src/data_analysis_agent/worker/health.py and worker/cli.py: implement the worker health command.
- .dockerignore, Dockerfile, frontend/Dockerfile, and frontend/nginx.conf: define backend and frontend images.
- compose.yaml, compose.https.yaml, .env.compose.example, and deploy/nginx/*: define services, secrets, routing, and TLS.
- scripts/generate-local-certificate.ps1 and scripts/generate-local-certificate.sh: create local certificates.
- .github/workflows/ci.yml and mypy.ini: define the CI/type-check policy.
- tests/config/test_settings_contract.py, tests/storage/test_factory.py, tests/api/test_health.py, tests/worker/test_health.py, tests/deployment/test_contract.py, and tests/packaging/test_dependency_constraints.py: test each deployment boundary.
- docs/deployment/local-compose.md, README.md, sdd/m20-task-plan.md, sdd/m20-progress.md, and sdd/m20-findings.md: document and record the result.

---

### Task 1: Stabilize Python Dependencies and Import the M15 Frontend

**Files:**
- Modify: pyproject.toml
- Create: requirements/constraints-py312.txt
- Create: mypy.ini
- Create: tests/packaging/test_dependency_constraints.py
- Import: frontend/ from the codex/m15-frontend worktree, including its package lock, Vite config, source, public assets, and tests.

**Interfaces:**
- Python install command: python -m pip install --constraint requirements/constraints-py312.txt -e .[dev,api,worker]
- Frontend commands: npm ci, npm run typecheck, npm run test, npm run build.
- Targeted type command: python -m mypy --config-file mypy.ini src/data_analysis_agent/config/settings.py src/data_analysis_agent/config/logging.py src/data_analysis_agent/api/health.py src/data_analysis_agent/worker/health.py.

- [ ] Step 1: Add the dependency regression test.

    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]

    def test_sqlalchemy_is_constrained_below_the_known_enum_regression():
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        constraints = (ROOT / "requirements" / "constraints-py312.txt").read_text(
            encoding="utf-8"
        )
        assert '"SQLAlchemy>=2.0,<2.1"' in metadata
        assert "SQLAlchemy<2.1" in constraints

    def test_mypy_is_available_through_the_dev_extra():
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert '"mypy>=1.11,<2.0"' in metadata

- [ ] Step 2: Run the focused test and verify it fails.

    .\.venv\Scripts\python.exe -m pytest tests/packaging/test_dependency_constraints.py -q

  Expected: failure because the SQLAlchemy range, constraints file, and mypy extra do not yet exist.

- [ ] Step 3: Add the dependency boundary and targeted mypy configuration.

Change SQLAlchemy in pyproject.toml to SQLAlchemy>=2.0,<2.1. Add mypy>=1.11,<2.0 to the dev extra. Create the constraints file from the tested Python 3.12 pip freeze result. It must contain the resolved application dependency versions, no credentials, no local paths, and at least:

    # Generated for Python 3.12 after M20 baseline verification.
    SQLAlchemy<2.1

Create mypy.ini:

    [mypy]
    python_version = 3.12
    show_error_codes = true
    ignore_missing_imports = true
    follow_imports = skip
    check_untyped_defs = true
    warn_unused_ignores = true

- [ ] Step 4: Import the approved M15 frontend files.

Copy the tracked frontend tree from codex/m15-frontend into this branch. Preserve package-lock.json, the Vite alias, the relative /api default, React tests, and public assets. Do not copy the worktree root README, environment files, Python files, or generated output. Keep these scripts:

    "build": "tsc -b && vite build"
    "typecheck": "tsc -b --pretty false"
    "test": "vitest run"

- [ ] Step 5: Reinstall and run dependency checks.

    .\.venv\Scripts\python.exe -m pip install -e ".[dev,api,worker]" --constraint requirements/constraints-py312.txt
    .\.venv\Scripts\python.exe -m pytest tests/packaging/test_dependency_constraints.py -q
    Push-Location frontend
    npm ci
    npm run typecheck
    npm run test
    npm run build
    Pop-Location

Expected: the packaging test, frontend type check, Vitest suite, and Vite build pass. SQLAlchemy resolves below 2.1.

- [ ] Step 6: Commit.

    git add pyproject.toml requirements/constraints-py312.txt mypy.ini tests/packaging/test_dependency_constraints.py frontend
    git commit -m "build: stabilize dependencies and add frontend workspace"

### Task 2: Add Explicit Storage Selection and Safe Runtime Configuration

**Files:**
- Modify: src/data_analysis_agent/config/settings.py
- Modify: src/data_analysis_agent/storage/factory.py
- Modify: src/data_analysis_agent/storage/__init__.py
- Modify: src/data_analysis_agent/storage/local.py
- Modify: src/data_analysis_agent/storage/s3.py
- Modify: tests/config/test_settings_contract.py
- Modify: tests/storage/test_factory.py

**Interfaces:**
- Settings.storage_backend is Literal["local", "s3"].
- Development and test default to local; production defaults to s3; Compose explicitly sets STORAGE_BACKEND=s3.
- build_storage(settings) returns LocalFileStorage or S3Storage based on storage_backend.
- Storage.healthcheck() -> None is available to readiness.

- [ ] Step 1: Add failing settings/factory tests.

    def test_storage_backend_defaults_to_local_for_test(tmp_path):
        settings = load_settings(app_env="test", environ={}, dotenv_dir=tmp_path)
        assert settings.storage_backend == "local"

    def test_storage_backend_selects_s3_in_development(tmp_path):
        settings = load_settings(
            app_env="development",
            environ={
                "STORAGE_BACKEND": "s3",
                "STORAGE_ENDPOINT": "http://minio:9000",
                "STORAGE_BUCKET": "data-analysis",
                "STORAGE_ACCESS_KEY_ID": "minioadmin",
                "STORAGE_SECRET_ACCESS_KEY": "minioadmin123",
            },
            dotenv_dir=tmp_path,
        )
        storage = build_storage(settings)
        assert storage.__class__.__name__ == "S3Storage"

    def test_invalid_storage_backend_is_rejected(tmp_path):
        with pytest.raises(ConfigurationError, match="STORAGE_BACKEND"):
            load_settings(
                app_env="development",
                environ={"STORAGE_BACKEND": "filesystem"},
                dotenv_dir=tmp_path,
            )

Extend existing redaction tests to assert that storage_signing_secret never appears in repr(settings) or settings.to_dict().

- [ ] Step 2: Run focused tests and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/config/test_settings_contract.py tests/storage/test_factory.py -q

Expected: failures because Settings has no storage_backend and the factory derives storage only from APP_ENV.

- [ ] Step 3: Implement settings parsing.

    VALID_STORAGE_BACKENDS = {"local", "s3"}

    def _storage_backend(values, environment):
        selected = (_nonblank(values.get("STORAGE_BACKEND")) or (
            "s3" if environment == "production" else "local"
        )).lower()
        if selected not in VALID_STORAGE_BACKENDS:
            raise ConfigurationError("STORAGE_BACKEND must be one of local or s3")
        if environment == "production" and selected != "s3":
            raise ConfigurationError("STORAGE_BACKEND must be s3 in production")
        return selected

Store the value in Settings, include it in to_dict, and retain existing URL/key redaction. When STORAGE_BACKEND=s3, require STORAGE_ENDPOINT and STORAGE_BUCKET in production.

- [ ] Step 4: Implement factory selection and health methods.

    def build_storage(settings):
        signing_secret = (
            settings.storage_signing_secret.encode("utf-8")
            if settings.storage_signing_secret
            else None
        )
        if settings.storage_backend == "local":
            return LocalFileStorage(
                settings.storage_local_root,
                signing_secret=signing_secret,
            )
        if not _has_value(settings.storage_endpoint) or not _has_value(
            settings.storage_bucket
        ):
            raise ConfigurationError(
                "STORAGE_ENDPOINT and STORAGE_BUCKET are required for the s3 backend"
            )
        from .s3 import S3Storage
        return S3Storage(
            bucket=settings.storage_bucket,
            endpoint=settings.storage_endpoint,
            region=settings.storage_region,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
        )

Add healthcheck() to the Storage protocol. LocalFileStorage.healthcheck creates its root and converts OSError to StorageError. S3Storage.healthcheck calls head_bucket(Bucket=self._bucket) and converts provider exceptions to the existing sanitized backend error. Never expose provider exception text.

- [ ] Step 5: Run focused tests and mypy.

    .\.venv\Scripts\python.exe -m pytest tests/config/test_settings_contract.py tests/storage/test_factory.py -q
    .\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/config/settings.py

- [ ] Step 6: Commit.

    git add src/data_analysis_agent/config/settings.py src/data_analysis_agent/storage src/data_analysis_agent/config/__init__.py tests/config/test_settings_contract.py tests/storage/test_factory.py
    git commit -m "feat: make object storage backend explicit"

### Task 3: Add JSON Logging and API Live/Readiness Probes

**Files:**
- Create: src/data_analysis_agent/config/logging.py
- Modify: src/data_analysis_agent/config/settings.py
- Modify: src/data_analysis_agent/config/__init__.py
- Create: src/data_analysis_agent/api/health.py
- Modify: src/data_analysis_agent/api/app.py
- Create: tests/api/test_health.py
- Modify: tests/config/test_settings_contract.py

**Interfaces:**
- configure_logging(settings) installs JsonLogFormatter on the application logger.
- check_readiness(application: APIApplication) -> tuple[bool, dict[str, str]] returns only ok/unavailable statuses.
- GET /health/live returns {"status": "ok"} with HTTP 200 without dependencies.
- GET /health/ready returns HTTP 200 only when database, redis, and storage are ok; otherwise HTTP 503 with no exception text or secrets.

- [ ] Step 1: Add failing tests.

    def test_live_probe_does_not_require_dependencies(fake_application):
        client = TestClient(create_app(fake_application))
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_probe_returns_503_and_safe_statuses(monkeypatch, fake_application):
        monkeypatch.setattr(
            "data_analysis_agent.api.health.check_database", lambda database: False
        )
        monkeypatch.setattr(
            "data_analysis_agent.api.health.check_redis", lambda redis_url: True
        )
        monkeypatch.setattr(
            "data_analysis_agent.api.health.check_storage", lambda application: True
        )
        response = TestClient(create_app(fake_application)).get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "components": {
                "database": "unavailable",
                "redis": "ok",
                "storage": "ok",
            },
        }

Add a formatter test that logs a request_id and task_id, asserts the rendered record is JSON, and asserts API keys, database URLs, and passwords are absent.

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/api/test_health.py tests/config/test_settings_contract.py -q

Expected: health routes are absent and the formatter is not JSON.

- [ ] Step 3: Implement JsonLogFormatter.

The formatter emits timestamp, level, logger, message, request_id, and task_id only:

    class JsonLogFormatter(logging.Formatter):
        def format(self, record):
            payload = {
                "timestamp": datetime.fromtimestamp(
                    record.created, timezone.utc
                ).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            for field_name in ("request_id", "task_id"):
                value = getattr(record, field_name, None)
                if isinstance(value, (str, int)) and value:
                    payload[field_name] = value
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

Change configure_logging to install this formatter once, retain the configured level and test-compatible propagation, and avoid arbitrary record attributes.

- [ ] Step 4: Implement bounded dependency checks and routes.

Create check_database(database), check_redis(redis_url), check_storage(application), and check_readiness(application) in api/health.py. Database uses SELECT 1. Redis uses socket_connect_timeout=1 and socket_timeout=1, then closes the client. Storage calls application.storage.healthcheck(). Every exception becomes False; no exception text is returned.

Register /health/live and /health/ready in create_app outside the /api prefix. They must not require authentication.

- [ ] Step 5: Run focused tests and mypy.

    .\.venv\Scripts\python.exe -m pytest tests/api/test_health.py tests/api/test_app.py tests/config/test_settings_contract.py -q
    .\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/config/logging.py src/data_analysis_agent/api/health.py

- [ ] Step 6: Commit.

    git add src/data_analysis_agent/config src/data_analysis_agent/api/app.py src/data_analysis_agent/api/health.py tests/api/test_health.py tests/config/test_settings_contract.py
    git commit -m "feat: add safe logging and dependency health probes"

### Task 4: Add the Worker Health Command and Bounded Database Connections

**Files:**
- Create: src/data_analysis_agent/worker/health.py
- Modify: src/data_analysis_agent/worker/cli.py
- Modify: src/data_analysis_agent/persistence/database.py
- Create: tests/worker/test_health.py
- Modify: tests/worker/test_celery_adapter.py

**Interfaces:**
- worker_healthcheck(settings) -> bool checks database and Redis.
- data-analysis-agent-worker --env development --healthcheck exits 0 when both dependencies respond and 1 otherwise.
- Normal worker startup and stale-task recovery remain unchanged.

- [ ] Step 1: Write failing tests.

    def test_worker_healthcheck_succeeds_when_dependencies_are_ready(monkeypatch, settings):
        monkeypatch.setattr(
            "data_analysis_agent.worker.health.database_ready",
            lambda configured: True,
        )
        monkeypatch.setattr(
            "data_analysis_agent.worker.health.redis_ready",
            lambda configured: True,
        )
        assert worker_healthcheck(settings) is True

    def test_worker_healthcheck_fails_when_redis_is_unavailable(monkeypatch, settings):
        monkeypatch.setattr(
            "data_analysis_agent.worker.health.database_ready",
            lambda configured: True,
        )
        monkeypatch.setattr(
            "data_analysis_agent.worker.health.redis_ready",
            lambda configured: False,
        )
        assert worker_healthcheck(settings) is False

Add a parser assertion for --healthcheck and a main(["--healthcheck"]) test that returns 1 when the probe is false.

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/worker/test_health.py tests/worker/test_celery_adapter.py -q

Expected: import or parser failures because the health module and flag do not exist.

- [ ] Step 3: Implement the checks and CLI branch.

Implement database_ready with Database.from_settings and SELECT 1, and redis_ready with redis.Redis.from_url(..., socket_connect_timeout=1, socket_timeout=1).ping(). Always dispose the database engine and close Redis. In main, load settings and configure logging first; if args.healthcheck is true, return 0 if worker_healthcheck(settings) else 1 without initializing Celery.

Add this parser option:

    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="check the worker database and Redis dependencies and exit",
    )

For MySQL engines, pass connect_args={"connect_timeout": 3} to create_engine. Keep SQLite behavior unchanged.

- [ ] Step 4: Run focused tests and mypy.

    .\.venv\Scripts\python.exe -m pytest tests/worker/test_health.py tests/worker/test_celery_adapter.py tests/database/test_database_config.py -q
    .\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/worker/health.py

- [ ] Step 5: Commit.

    git add src/data_analysis_agent/worker/health.py src/data_analysis_agent/worker/cli.py src/data_analysis_agent/persistence/database.py tests/worker/test_health.py tests/worker/test_celery_adapter.py
    git commit -m "feat: add worker dependency health command"

### Task 5: Build the Backend and Frontend Images

**Files:**
- Create: .dockerignore
- Create: Dockerfile
- Create: frontend/Dockerfile
- Create: frontend/nginx.conf
- Modify: tests/deployment/test_contract.py

**Interfaces:**
- Dockerfile produces one non-root image for backend, worker, and migrate.
- Backend image contains src, alembic, alembic.ini, and constrained Python dependencies.
- frontend/Dockerfile accepts only public VITE_API_BASE_URL, defaults it to /api, and serves /healthz.
- No Dockerfile contains API keys, database passwords, MinIO keys, or signing secrets.

- [ ] Step 1: Add static Dockerfile tests.

    def test_dockerfiles_use_runtime_secret_injection():
        backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
        combined = backend + frontend
        assert "FROM python:3.12-slim" in backend
        assert "FROM node:" in frontend
        assert "npm ci" in frontend
        assert "VITE_API_BASE_URL" in frontend
        for secret_name in (
            "OPENAI_API_KEY",
            "MYSQL_PASSWORD",
            "MINIO_ROOT_PASSWORD",
            "STORAGE_SIGNING_SECRET",
        ):
            assert f"ARG {secret_name}" not in combined

    def test_dockerignore_excludes_local_credentials_and_outputs():
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        assert ".env.compose" in ignored
        assert ".venv" in ignored
        assert "deploy/certs" in ignored
        assert "outputs" in ignored

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q

Expected: failure because the image files do not exist.

- [ ] Step 3: Create the backend Dockerfile.

Use Python 3.12-slim, set PYTHONDONTWRITEBYTECODE=1, PYTHONUNBUFFERED=1, and PIP_NO_CACHE_DIR=1, copy the constraints and packaging files, copy `src/`, `alembic/`, and `alembic.ini`, then install the API and worker extras under the constraints so normal package discovery can see the source tree. Create a non-root app user with uid 10001 and writable /app/outputs and /app/outputs/datasets. Expose 8000 and use this default command:

    CMD ["uvicorn", "data_analysis_agent.api.app:create_app", "--factory",
         "--host", "0.0.0.0", "--port", "8000"]

Runtime Compose commands override this for migrate and worker.

- [ ] Step 4: Create the frontend image and runtime config.

Use a Node 22 build stage, run npm ci, build with VITE_API_BASE_URL=/api by default, and copy dist into nginx:1.27-alpine. The frontend Nginx server listens on 8080, returns 200 and ok from /healthz, and uses try_files $uri $uri/ /index.html for SPA routing. Do not copy .env files or backend secrets into the image.

- [ ] Step 5: Run static checks and frontend verification.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q
    Push-Location frontend
    npm run typecheck
    npm run test
    npm run build
    Pop-Location

- [ ] Step 6: Commit.

    git add .dockerignore Dockerfile frontend/Dockerfile frontend/nginx.conf tests/deployment/test_contract.py
    git commit -m "build: add backend and frontend container images"

### Task 6: Define MySQL, Redis, MinIO, Migration, and Application Compose Services

**Files:**
- Create: compose.yaml
- Create: .env.compose.example
- Modify: .gitignore
- Modify: tests/deployment/test_contract.py

**Interfaces:**
- docker compose --env-file .env.compose -f compose.yaml up -d --build starts the HTTP stack.
- migrate runs alembic upgrade head and must exit successfully before backend and worker start.
- minio-init creates STORAGE_BUCKET idempotently.
- backend runs Uvicorn on internal 8000; worker runs data-analysis-agent-worker with stale recovery.
- Native health checks use internal service DNS and bounded retries.

- [ ] Step 1: Add Compose contract tests.

    import yaml

    def test_compose_declares_the_approved_services():
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        assert set(compose["services"]) == {
            "frontend", "backend", "worker", "migrate", "mysql", "redis",
            "minio", "minio-init", "reverse-proxy",
        }
        assert compose["services"]["mysql"]["image"].startswith("mysql:8.0")
        assert compose["services"]["redis"]["command"] == "redis-server --appendonly yes"
        assert compose["services"]["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert compose["services"]["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    def test_compose_uses_persistent_named_volumes_and_nonconflicting_ports():
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        assert {"mysql_data", "redis_data", "minio_data"} <= set(compose["volumes"])
        assert "3307:3306" in compose["services"]["mysql"]["ports"]
        assert "8080:80" in compose["services"]["reverse-proxy"]["ports"]

    def test_compose_does_not_place_secrets_in_build_args():
        text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY:" not in text.split("build:", 1)[-1]
        assert "MYSQL_PASSWORD" in text
        assert "STORAGE_SIGNING_SECRET" in text

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q

Expected: failure because compose.yaml and .env.compose.example are absent.

- [ ] Step 3: Create the safe environment template.

Use safe local placeholders only:

    APP_ENV=development
    OPENAI_API_KEY=
    OPENAI_BASE_URL=https://api.deepseek.com
    OPENAI_MODEL=deepseek-chat
    MYSQL_ROOT_PASSWORD=change-me-root
    MYSQL_DATABASE=data_analysis
    MYSQL_USER=data_analysis
    MYSQL_PASSWORD=change-me-app
    DATABASE_URL=mysql+pymysql://data_analysis:change-me-app@mysql:3306/data_analysis
    REDIS_URL=redis://redis:6379/0
    STORAGE_BACKEND=s3
    STORAGE_ENDPOINT=http://minio:9000
    STORAGE_BUCKET=data-analysis
    STORAGE_REGION=us-east-1
    STORAGE_ACCESS_KEY_ID=minioadmin
    STORAGE_SECRET_ACCESS_KEY=minioadmin123
    STORAGE_SIGNING_SECRET=change-me-to-a-long-random-value
    STORAGE_URL_EXPIRY=300
    STORAGE_RETENTION_DAYS=30
    EXECUTION_BACKEND=local
    EXECUTION_NETWORK_MODE=none
    LOG_LEVEL=INFO

Document that the real file must replace every change-me value and that changing MYSQL_PASSWORD requires changing DATABASE_URL too.

- [ ] Step 4: Write compose.yaml.

Use pinned images mysql:8.0.44, redis:7.4-alpine, a pinned MinIO server, and a matching pinned minio/mc image. Define exactly frontend, backend, worker, migrate, mysql, redis, minio, minio-init, and reverse-proxy. Use the named volumes mysql_data, redis_data, and minio_data.

MySQL receives MYSQL_ROOT_PASSWORD, MYSQL_DATABASE, MYSQL_USER, and MYSQL_PASSWORD, maps 3307:3306, persists /var/lib/mysql, and checks mysqladmin ping with the root password. Redis runs redis-server --appendonly yes, persists /data, and checks redis-cli ping. MinIO runs server /data --console-address :9001, persists /data, and checks /minio/health/ready. minio-init waits for MinIO, runs mc alias set and mc mb --ignore-existing, and exits successfully.

migrate receives DATABASE_URL and APP_ENV, waits for MySQL health, and runs alembic upgrade head. backend and worker receive the same database, Redis, MinIO, storage-signing, and LLM settings; they wait for healthy MySQL, Redis, MinIO, successful minio-init, and successful migrate. The backend health check calls http://127.0.0.1:8000/health/ready. The worker health check runs data-analysis-agent-worker --env development --healthcheck. Frontend exposes only internal port 8080. reverse-proxy maps 8080:80 and routes to frontend:8080 and backend:8000.

Set json-file logging with max-size 10m and max-file 3 on application services. Add a private default network and the three named volumes. Escape Compose variable references used inside container shell commands so they are evaluated in the container.

- [ ] Step 5: Extend ignore rules and run checks.

Add .env.compose, deploy/certs/*.pem, deploy/certs/*.key, frontend/node_modules/, and frontend/dist/ to .gitignore.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py tests/packaging/test_dependency_constraints.py -q

- [ ] Step 6: Commit.

    git add compose.yaml .env.compose.example .gitignore tests/deployment/test_contract.py
    git commit -m "feat: add persistent local compose services"

### Task 7: Add Nginx Routing and Local HTTPS

**Files:**
- Create: deploy/nginx/reverse-proxy.conf
- Create: deploy/nginx/reverse-proxy-https.conf
- Create: compose.https.yaml
- Create: deploy/certs/.gitkeep
- Create: scripts/generate-local-certificate.ps1
- Create: scripts/generate-local-certificate.sh
- Modify: tests/deployment/test_contract.py

**Interfaces:**
- HTTP localhost:8080 serves the frontend, proxies /api/ to backend:8000, and returns 200 from /healthz.
- HTTPS override listens on localhost:8443 and mounts deploy/certs/local.crt and deploy/certs/local.key read-only.
- Certificate helpers create exactly those two files and do not overwrite them without an explicit force flag.

- [ ] Step 1: Add failing proxy tests.

    def test_reverse_proxy_routes_frontend_api_and_healthz():
        config = (ROOT / "deploy" / "nginx" / "reverse-proxy.conf").read_text(encoding="utf-8")
        assert "proxy_pass http://frontend:8080" in config
        assert "proxy_pass http://backend:8000" in config
        assert "location = /healthz" in config
        assert "X-Request-ID" in config

    def test_https_override_mounts_ignored_local_certificates():
        override = yaml.safe_load((ROOT / "compose.https.yaml").read_text(encoding="utf-8"))
        proxy = override["services"]["reverse-proxy"]
        assert "8443:443" in proxy["ports"]
        assert any("deploy/certs:/etc/nginx/certs:ro" in item for item in proxy["volumes"])
        https = (ROOT / "deploy/nginx/reverse-proxy-https.conf").read_text(encoding="utf-8")
        assert "ssl_certificate /etc/nginx/certs/local.crt" in https

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q

Expected: failure because proxy and HTTPS files do not exist.

- [ ] Step 3: Implement HTTP proxy configuration.

Define frontend:8080 and backend:8000 upstreams. Add location = /healthz returning ok, location /api/ with proxy_pass to backend, and location / with proxy_pass to frontend. Preserve Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto, and X-Request-ID. Use bounded proxy connect/read timeouts and do not log credentials.

- [ ] Step 4: Implement HTTPS and certificate helpers.

compose.https.yaml replaces the reverse-proxy config, adds 8443:443, and mounts ./deploy/certs:/etc/nginx/certs:ro. The HTTPS server listens on 443 ssl and references the two certificate files while retaining the HTTP routes. It must fail clearly if the files are missing.

The PowerShell helper uses New-SelfSignedCertificate for DNS=localhost and exports PEM files through an available OpenSSL command. The Bash helper runs:

    openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
      -keyout deploy/certs/local.key \
      -out deploy/certs/local.crt \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

Both helpers create the directory, fail when crypto tooling is missing, and preserve existing files unless an explicit force flag is supplied.

- [ ] Step 5: Run static checks and ignore verification.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q
    git check-ignore deploy/certs/local.crt deploy/certs/local.key .env.compose

Expected: tests pass and all three paths are ignored. Nginx execution is deferred to Docker-enabled CI.

- [ ] Step 6: Commit.

    git add deploy/nginx compose.https.yaml deploy/certs/.gitkeep scripts/generate-local-certificate.ps1 scripts/generate-local-certificate.sh .gitignore tests/deployment/test_contract.py
    git commit -m "feat: add reverse proxy and local https"

### Task 8: Add CI for Tests, Types, Migrations, Compose, and Images

**Files:**
- Create: .github/workflows/ci.yml
- Modify: tests/deployment/test_contract.py

**Interfaces:**
- Pull requests and pushes run backend, frontend, deployment-contract, and image-build checks.
- CI uses a dummy LLM key and fake/offline tests.
- Docker CI validates base and HTTPS Compose configuration and builds backend/frontend images without secret build arguments.
- Optional publishing is enabled only when registry variables and secrets are present.

- [ ] Step 1: Add a workflow contract test.

    def test_ci_runs_tests_types_and_image_builds():
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        assert "pytest" in workflow
        assert "mypy" in workflow
        assert "npm run typecheck" in workflow
        assert "npm run test" in workflow
        assert "docker compose" in workflow
        assert "docker build" in workflow or "build-push-action" in workflow
        assert "OPENAI_API_KEY: test-only-key" in workflow

- [ ] Step 2: Run and verify failure.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q

Expected: failure because the workflow does not exist.

- [ ] Step 3: Create Python/frontend jobs.

The Python job uses setup-python 3.12, installs with the constraints file and .[dev,api,worker], runs pytest with APP_ENV=test and OPENAI_API_KEY=test-only-key, and runs targeted mypy. The frontend job runs npm ci, npm run typecheck, npm run test, and npm run build from frontend/.

- [ ] Step 4: Create Docker/configuration jobs.

Run these commands in CI:

    docker compose --env-file .env.compose.example -f compose.yaml config
    docker compose --env-file .env.compose.example -f compose.yaml -f compose.https.yaml config
    docker build --file Dockerfile --tag data-analysis-agent:ci .
    docker build --file frontend/Dockerfile --tag data-analysis-agent-frontend:ci frontend

Start MySQL, Redis, and MinIO in an integration phase, wait for health, run migrate, probe backend /health/ready, and run the worker --healthcheck command. Run alembic check with a configured safe MySQL URL. Do not define secret build args.

- [ ] Step 5: Add optional registry publishing.

Pull requests stay build-only. On pushes to main, publish SHA-tagged images only when CONTAINER_REGISTRY is configured and registry credentials exist in GitHub Secrets.

- [ ] Step 6: Run local workflow-contract and type checks.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py tests/packaging/test_dependency_constraints.py -q
    .\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/config/settings.py src/data_analysis_agent/config/logging.py src/data_analysis_agent/api/health.py src/data_analysis_agent/worker/health.py

- [ ] Step 7: Commit.

    git add .github/workflows/ci.yml tests/deployment/test_contract.py
    git commit -m "ci: test and build deployment artifacts"

### Task 9: Document Startup, Restart, HTTPS, and Verification

**Files:**
- Create: docs/deployment/local-compose.md
- Modify: README.md
- Modify: sdd/m20-task-plan.md
- Modify: sdd/m20-progress.md
- Modify: sdd/m20-findings.md

- [ ] Step 1: Write the deployment guide.

Include prerequisites, secret replacement, the following commands, and expected outcomes:

    Copy-Item .env.compose.example .env.compose
    docker compose --env-file .env.compose -f compose.yaml config
    docker compose --env-file .env.compose -f compose.yaml up -d --build
    Invoke-WebRequest http://localhost:8080/healthz
    docker compose --env-file .env.compose -f compose.yaml ps
    docker compose --env-file .env.compose -f compose.yaml logs backend worker migrate

For direct API readiness and migration inspection:

    docker compose --env-file .env.compose -f compose.yaml exec backend wget -qO- http://127.0.0.1:8000/health/ready
    docker compose --env-file .env.compose -f compose.yaml exec migrate alembic current

For restart persistence:

    docker compose --env-file .env.compose -f compose.yaml down
    docker compose --env-file .env.compose -f compose.yaml up -d
    docker compose --env-file .env.compose -f compose.yaml exec backend wget -qO- http://127.0.0.1:8000/health/ready

Explain that down preserves named volumes and down -v removes MySQL, Redis, and MinIO data. Explain the self-signed certificate warning and the absence of automatic public renewal:

    pwsh ./scripts/generate-local-certificate.ps1
    docker compose --env-file .env.compose -f compose.yaml -f compose.https.yaml up -d --build

Mention host MySQL conflict avoidance at 3307 and that an empty OPENAI_API_KEY supports health-only startup but analysis calls require a key.

- [ ] Step 2: Link the guide from README.

Add an M20 Docker Compose section near the M13 startup section, link to docs/deployment/local-compose.md, and show the one-line startup command. Keep existing SQLite instructions.

- [ ] Step 3: Update planning records.

Mark design and plan stages complete in sdd/m20-task-plan.md. Record Docker as the only unverified runtime prerequisite in sdd/m20-findings.md. Append each verification result to sdd/m20-progress.md. Do not overwrite the historical root task_plan.md, findings.md, or progress.md.

- [ ] Step 4: Run documentation and contract checks.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q
    rg -n "local-compose|compose.yaml|down -v|OPENAI_API_KEY|3307|8443" README.md docs/deployment/local-compose.md

- [ ] Step 5: Commit.

    git add README.md docs/deployment/local-compose.md sdd/m20-task-plan.md sdd/m20-progress.md sdd/m20-findings.md
    git commit -m "docs: document local compose deployment"

### Task 10: Execute the Full Verification Matrix and Close M20

**Files:**
- Modify: sdd/m20-progress.md
- Modify: sdd/m20-findings.md only for newly observed evidence

- [ ] Step 1: Run focused Python verification.

    .\.venv\Scripts\python.exe -m pytest tests/config/test_settings_contract.py tests/storage/test_factory.py tests/api/test_health.py tests/worker/test_health.py tests/deployment/test_contract.py tests/packaging/test_dependency_constraints.py -q

Expected: all M20-focused tests pass.

- [ ] Step 2: Run the complete backend suite under the constrained environment.

    .\.venv\Scripts\python.exe -m pytest -q

Expected: the prior SQLAlchemy 2.1.1 enum-autoflush failure is gone. Record the exact pass/skip count and any unrelated pre-existing failure instead of masking it.

- [ ] Step 3: Run frontend verification.

    Push-Location frontend
    npm ci
    npm run typecheck
    npm run test
    npm run build
    Pop-Location

Expected: all frontend checks pass and frontend/dist remains ignored.

- [ ] Step 4: Run structural Compose and secret-boundary checks.

    .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q
    git diff --check
    git grep -n -I -E "(sk-[A-Za-z0-9]{20,}|OPENAI_API_KEY=[^[:space:]]+|MYSQL_PASSWORD=[^[:space:]]+|STORAGE_SIGNING_SECRET=[^[:space:]]+)" -- ":!docs/superpowers/plans/*" ":!.env.compose.example"

Expected: no real-looking secrets are found; placeholders remain only in the explicitly committed example file.

- [ ] Step 5: Run Docker verification on a Docker-enabled runner.

    docker compose --env-file .env.compose.example -f compose.yaml config
    docker compose --env-file .env.compose.example -f compose.yaml -f compose.https.yaml config
    docker compose --env-file .env.compose.example -f compose.yaml build
    docker compose --env-file .env.compose.example -f compose.yaml up -d
    docker compose --env-file .env.compose.example -f compose.yaml exec backend wget -qO- http://127.0.0.1:8000/health/ready
    docker compose --env-file .env.compose.example -f compose.yaml exec worker data-analysis-agent-worker --env development --healthcheck
    docker compose --env-file .env.compose.example -f compose.yaml down
    docker compose --env-file .env.compose.example -f compose.yaml up -d
    docker compose --env-file .env.compose.example -f compose.yaml exec migrate alembic current
    docker compose --env-file .env.compose.example -f compose.yaml down

Expected: all services become healthy and the second startup keeps migration state and named-volume data. On this machine, record Docker as unverified because the CLI is absent.

- [ ] Step 6: Final review and verification record.

    git status --short --branch
    git log --oneline --decorate -12
    git diff main...HEAD --stat
    git diff --check
    git add sdd/m20-progress.md sdd/m20-findings.md
    git commit -m "chore: record M20 verification results"

M20 is complete only when the new-machine guide, migration gate, persistent volume contract, CI workflow, secret boundary, health probes, and available verification evidence are present.

## Self-Review Checklist

- [ ] Spec coverage: Tasks 1-2 cover dependency reproducibility, environment variables, and MinIO storage selection.
- [ ] Spec coverage: Tasks 3-4 cover stdout JSON logs, API live/readiness, worker health, and bounded database/Redis checks.
- [ ] Spec coverage: Tasks 5-6 cover backend/frontend Dockerfiles, Compose, MySQL, Redis AOF, MinIO bucket initialization, migrations, volumes, and service ordering.
- [ ] Spec coverage: Task 7 covers reverse proxy, forwarding headers, HTTP, local HTTPS, and certificate generation.
- [ ] Spec coverage: Task 8 covers tests, targeted type checking, migration/config validation, image builds, and conditional image publishing.
- [ ] Spec coverage: Task 9 covers new-machine startup, restart persistence, destructive cleanup, and current-machine Docker limitations.
- [ ] Placeholder scan: every implementation step names its file, interface, command, and expected result.
- [ ] Type consistency: Settings.storage_backend, Storage.healthcheck, check_readiness, worker_healthcheck, and CLI --healthcheck are used consistently across tasks and tests.
- [ ] Secret boundary: no API key, database password, MinIO credential, or signing secret is copied into an image or committed outside the safe example file.
