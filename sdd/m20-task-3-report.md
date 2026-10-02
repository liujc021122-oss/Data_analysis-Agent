# M20 Task 3 Report: JSON Logging and API Health Probes

## Scope

Implemented Task 3 on the `m20-deployment` worktree from commit `9bbe20c`. The change covers application JSON logging and unauthenticated API liveness/readiness probes only. Compose, worker health, frontend, migrations, and historical reports were not modified.

## Implementation

- Added `JsonLogFormatter` in `src/data_analysis_agent/config/logging.py`.
  - Emits only `timestamp`, `level`, `logger`, `message`, and non-empty string/integer `request_id` and `task_id` fields.
  - Uses UTC ISO timestamps and compact UTF-8 JSON.
  - Preserves the existing `configure_logging(settings)` behavior for logger level, propagation, and repeated configuration without adding duplicate handlers.
- Moved `configure_logging` out of `settings.py` and re-exported it from both `settings.py` and `config/__init__.py`, preserving existing CLI and package import paths.
- Added `src/data_analysis_agent/api/health.py` with:
  - `check_database`, using `SELECT 1` through the existing database engine.
  - `check_redis`, using one-second connect and socket timeouts and closing the client.
  - `check_storage`, using the existing storage `healthcheck()` boundary.
  - `check_readiness`, returning only `ok` or `unavailable` component statuses.
  - `/health/live` and `/health/ready` routes.
- Registered the health router outside the `/api` prefix, so both routes are available without authentication. Readiness returns HTTP 200 only when all three checks pass and HTTP 503 otherwise, with no exception details.
- Added route and formatter contract tests.

## TDD evidence

The new tests were run before the implementation and failed during collection because `data_analysis_agent.config.logging` did not yet exist. After the implementation, the focused settings and health suite passed.

## Verification

- `\.venv\Scripts\python.exe -m pytest tests/api/test_health.py tests/api/test_app.py tests/config/test_settings_contract.py -q`
  - Result: `42 passed`
- `\.venv\Scripts\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/config/logging.py src/data_analysis_agent/api/health.py`
  - Result: `Success: no issues found in 2 source files`
- `git diff --check`
  - Result: no whitespace errors.

## Review notes

The readiness route intentionally reports an unconfigured Redis URL as unavailable. Health check exceptions are caught at each dependency boundary and are never included in the response. The formatter does not serialize arbitrary `LogRecord` attributes, preventing API keys, URLs, passwords, and other extra record fields from being emitted as structured fields.
