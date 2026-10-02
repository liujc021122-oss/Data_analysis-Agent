# M20 Task 9 report

## Status

Task 9 documentation is complete on branch `codex/m20-deployment`. The committed scope is limited to the requested deployment guide, focused README section, and M20 planning ledgers. No application or deployment implementation files were changed.

## Changes

- Created `docs/deployment/local-compose.md` with:
  - Docker Desktop and Compose v2 prerequisites.
  - `.env.compose` creation and replacement of all `change-me` secrets.
  - Empty `OPENAI_API_KEY` behavior for health-only startup and the requirement for analysis calls.
  - Exact Compose config, build/start, HTTP health, service status, logs, direct readiness, migration inspection, restart, and cleanup commands.
  - MySQL host port `3307`, with the internal Compose address remaining `mysql:3306`.
  - Optional self-signed local HTTPS generation and startup on port `8443`.
  - The expected browser certificate warning and the absence of automatic public certificate renewal.
  - The persistence difference between `down` and destructive `down -v`, including MySQL, Redis, and MinIO data.
  - The local verification boundary caused by Docker being unavailable on this machine.
- Added an M20 Docker Compose startup section to `README.md`, linked the full guide, showed the one-line startup command, and preserved the existing SQLite/M13 instructions.
- Updated `sdd/m20-task-plan.md` to mark the completed M20 implementation and documentation stages.
- Appended Task 9 evidence to `sdd/m20-progress.md`.
- Added the Task 9 verification finding to `sdd/m20-findings.md`, identifying Docker and Docker Compose as the only unverified runtime prerequisite.
- Preserved the historical root `task_plan.md`, `findings.md`, and `progress.md` files and prior M20 task report files.

## Verification

- `\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q`: 13 passed.
- Required `rg -n "local-compose|compose.yaml|down -v|OPENAI_API_KEY|3307|8443" README.md docs/deployment/local-compose.md`: all requested markers found.
- `git diff --cached --check`: passed before commit.
- Required command presence was reviewed against the brief, including config validation, startup, health/status/log inspection, direct readiness, migration current, restart, certificate generation, HTTPS startup, and destructive cleanup.
- Docker runtime verification was not run because the Docker CLI is not installed locally. This is recorded in the guide and M20 findings.

## Commit

`b18e1b9 docs: document local compose deployment`
