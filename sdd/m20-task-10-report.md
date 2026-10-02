# M20 Task 10 Report: Full Verification Matrix

## Status

Task 10 verification is recorded on `codex/m20-deployment`. M20 is not closed because the full backend suite has two failures. Docker and Compose runtime checks are unverified locally because the Docker CLI is absent.

## Verification results

| Check | Result |
| --- | --- |
| Focused M20 Python suite | 62 passed in 1.91s |
| Full constrained backend suite | 979 passed, 1 skipped, 2 failed in 78.85s |
| Frontend `npm ci` | Passed; 189 packages installed; 4 moderate audit advisories reported |
| Frontend typecheck | Passed |
| Frontend tests | 12 files, 30 tests passed |
| Frontend build | Passed; Vite produced `frontend/dist` |
| Deployment contract suite | 13 passed |
| Secret boundary scan | No real-looking committed secret found; placeholders and synthetic test values only |
| `git diff --check` | Passed |
| Docker/Compose | Unverified: `docker` command is absent |

The constrained environment is Python 3.12.7 with SQLAlchemy 2.0.54. The prior SQLAlchemy 2.1.1 enum-autoflush failure is gone.

## Backend failures

The failures are:

- `tests/test_cli_dataset_ids.py::test_build_dataset_resolver_rejects_production_local_storage`
- `tests/test_cli_dataset_ids.py::test_production_cli_dataset_id_fails_closed_before_database_creation`

Both tests construct settings with a legacy `SimpleNamespace` fixture that lacks `storage_signing_secret` and the other storage selection fields now consumed by `build_storage`. Production validation therefore raises `AttributeError` before the expected `ConfigurationError`. The CLI test returns 1 instead of the expected 2. This is an open compatibility regression and also blocks the CI backend job, which runs the same full suite.

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
