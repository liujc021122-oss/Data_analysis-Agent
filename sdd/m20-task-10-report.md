# M20 Task 10 Report: Full Verification Matrix

## Status

Task 10 verification is recorded on `codex/m20-deployment`. The initial full backend suite exposed a legacy settings compatibility regression, which was fixed in `506384f`; the final backend suite is green. Docker and Compose runtime checks are unverified locally because the Docker CLI is absent.

## Verification results

| Check | Result |
| --- | --- |
| Latest deployment/configuration/health-focused Python suite | 68 passed in 1.36s |
| Full constrained backend suite before compatibility fix | 979 passed, 1 skipped, 2 failed in 78.85s |
| Final constrained backend suite before audit timestamp fix | 984 passed, 1 skipped in 68.88s |
| Final constrained backend suite after audit timestamp fix | 988 passed, 1 skipped in 69.85s |
| Frontend `npm ci` | Passed; 189 packages installed; 4 moderate audit advisories reported |
| Frontend typecheck | Passed |
| Frontend tests | 12 files, 30 tests passed |
| Frontend build | Passed; Vite produced `frontend/dist` |
| Deployment contract suite | 13 passed |
| Secret boundary scan | No real-looking committed secret found; placeholders and synthetic test values only |
| `git diff --check` | Passed |
| Docker/Compose | Unverified: `docker` command is absent |

The constrained environment is Python 3.12.7 with SQLAlchemy 2.0.54. The prior SQLAlchemy 2.1.1 enum-autoflush failure is gone.

## Audit timestamp regression

The authentication audit test exposed a pre-existing reliability issue after repeated full-suite runs. On Windows, consecutive `datetime.now()` calls can return the same value. The test orders audit records by `occurred_at, event_id`; because UUIDs are random, equal timestamps could place `LOGGED_OUT` before `LOGIN_FAILED` even when the writes were sequential.

The regression test was run first and failed with a frozen clock. `AuditWriter` now allocates timestamps under a lock and advances an equal or backward candidate by one microsecond. This preserves write order within one writer instance without adding a schema migration. The authentication service test file passed five consecutive times, and the fresh full suite passed with `988 passed, 1 skipped`.

## Backend regression and resolution

The failures are:

- `tests/test_cli_dataset_ids.py::test_build_dataset_resolver_rejects_production_local_storage`
- `tests/test_cli_dataset_ids.py::test_production_cli_dataset_id_fails_closed_before_database_creation`

Both tests constructed settings with a legacy `SimpleNamespace` fixture that lacked `storage_signing_secret` and the other storage selection fields consumed by `build_storage`. Production validation therefore raised `AttributeError` before the expected `ConfigurationError`, and the CLI test returned 1 instead of 2. Commit `506384f` added safe legacy defaults at the storage factory boundary and regression tests. The final full suite no longer reproduces either failure.

## Frontend and generated files

`npm ci`, `npm run typecheck`, `npm run test`, and `npm run build` all completed successfully. The build output is ignored by Git. TypeScript created `frontend/tsconfig.tsbuildinfo` during verification; it was removed afterward.

## Deployment and secret checks

The deployment contract suite passed. The requested grep pattern matched Compose variable references, the README placeholder `your_api_key_here`, and synthetic test strings. Inspection of `.dockerignore`, Dockerfiles, Compose files, the HTTPS override, and the CI workflow found runtime secret injection without secret build arguments or committed credential values. The CI workflow contains Docker-enabled Compose config, integration/migration, and image-build jobs, but no CI execution result was available in this task.

## Docker boundary

The Docker commands in the brief were not run because `docker --version` fails with the command unavailable. Compose parsing, image builds, container health, migrations, proxy routing, HTTPS, and restart persistence remain unverified and must be checked on Docker-enabled CI or another Docker host.

## Self-review

- Spec coverage checklist reviewed against the Task 10 brief and prior Task 1–9 records.
- Only `sdd/m20-progress.md` and `sdd/m20-findings.md` received new evidence; application and deployment implementation files were not changed.
- Historical root planning files and prior task reports were preserved.
- The report records exact counts, the two failure identities and cause, frontend output, secret-scan interpretation, and the Docker limitation without claiming unavailable runtime results.
- The follow-up fix report records the RED/GREEN evidence and compatibility tests for `506384f`.
- The audit timestamp fix was verified with a dedicated RED/GREEN regression test and is included in the final full-suite count above.

## Final verification addendum (2026-10-02)

The final full backend run after the MySQL audit timestamp precision and SQLAlchemy cache-safety fixes reports `992 passed, 1 skipped`. The dedicated database precision test and API audit tests pass without the previous `UTCDateTimeMicrosecond` cache warning. The latest deployment/packaging contract suite reports `15 passed`; CI-targeted mypy reports no issues in four source files; frontend install, typecheck, 30 tests, and production build pass; and `git diff --check` passes.

The application keeps the generic `UTCDateTime` portable and applies `DATETIME(6)` only to MySQL audit-event timestamps. Docker/Compose runtime behavior remains unverified locally because Docker CLI is absent; the CI workflow is the verification path for image builds, service communication, migrations, health, proxy/HTTPS routing, and volume persistence.
