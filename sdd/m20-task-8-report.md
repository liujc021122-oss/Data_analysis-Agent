# M20 Task 8 Report: CI for Tests, Types, Migrations, Compose, and Images

## Scope

Implemented the Task 8 GitHub Actions workflow and its deployment contract test. The workflow runs on pull requests and pushes, keeps test LLM calls offline, validates the Compose files, exercises migrations and service readiness against safe CI-only MySQL/Redis/MinIO services, builds both container images, and publishes SHA-tagged images only when the push is to `main` and registry configuration is complete.

Only these task files were changed, plus this report:

- `.github/workflows/ci.yml`
- `tests/deployment/test_contract.py`
- `sdd/m20-task-8-report.md`

The existing `sdd/m20-*` planning and prior task report files were preserved.

## Workflow implementation

### Backend job

- Runs on pull requests and pushes.
- Uses `actions/setup-python@v5` with Python 3.12.
- Installs `.[dev,api,worker]` through `requirements/constraints-py312.txt`.
- Runs the full pytest suite with `APP_ENV=test` and the dummy `OPENAI_API_KEY: test-only-key`.
- Runs targeted mypy for settings, logging, API health, and Worker health modules.

### Frontend job

- Uses Node 22 and the committed lockfile.
- Runs `npm ci`, `npm run typecheck`, `npm run test`, and `npm run build` from `frontend/`.

### Deployment contract job

- Runs the deployment and dependency constraint contract tests.
- Validates both commands required by the deployment contract:
  - `docker compose --env-file .env.compose.example -f compose.yaml config`
  - `docker compose --env-file .env.compose.example -f compose.yaml -f compose.https.yaml config`

### Integration job

- Starts MySQL 8.0.44, Redis 7.4, and the pinned MinIO image as healthy GitHub service containers.
- Uses a CI-only MySQL user/password and a CI-only MinIO signing secret.
- Waits for all three services, creates the test bucket, runs `alembic upgrade head`, and verifies `alembic check`.
- Starts the backend and probes `/health/ready`.
- Runs `data-analysis-agent-worker --env test --healthcheck`.
- Keeps the LLM key as `test-only-key`; no real provider call is enabled by the workflow.

### Image and publishing jobs

- Builds the backend image with `docker build --file Dockerfile --tag data-analysis-agent:ci .`.
- Builds the frontend image with `docker build --file frontend/Dockerfile --tag data-analysis-agent-frontend:ci frontend`.
- No secret build arguments are defined or passed.
- A registry configuration job checks `vars.CONTAINER_REGISTRY`, `secrets.REGISTRY_USERNAME`, and `secrets.REGISTRY_PASSWORD` without printing their values.
- Publishing requires all checks, a push to `refs/heads/main`, and an enabled registry configuration. Published tags use `github.sha`.

## TDD and verification

The new contract test was added first and failed as expected because `.github/workflows/ci.yml` did not exist:

```text
1 failed, 12 passed
FileNotFoundError: .../.github/workflows/ci.yml
```

After the workflow was added, the focused contract and dependency checks passed:

```text
15 passed in 0.24s
```

Additional local checks passed:

- Targeted mypy: `Success: no issues found in 4 source files`.
- Workflow YAML parsed successfully with PyYAML.
- Frontend typecheck passed.
- Frontend Vitest: 12 test files and 30 tests passed.
- Frontend production build passed.
- `git diff --check` passed.

Docker and actionlint are not installed in the current development environment. Compose execution and image builds are therefore delegated to the Docker-enabled CI jobs defined here; the workflow text, YAML syntax, and deployment contract assertions were verified locally.

## Review

The staged scope contains only the requested CI workflow, its contract test, and this report. No application code, Compose file, Dockerfile, frontend source, proxy configuration, or prior planning file was modified.
