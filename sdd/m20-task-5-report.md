# M20 Task 5 Report: Backend and Frontend Images

## Scope

Implemented Task 5 on the `m20-deployment` worktree from commit `f2a4f26`. Existing `sdd/m20-*` planning and prior task report files were preserved.

## Changes

- Added `.dockerignore` entries for local environment files, `.env.compose`, virtual environments, deployment certificates, generated outputs, frontend dependencies, and other local artifacts.
- Added the backend `Dockerfile` using `python:3.12-slim`, constrained Python 3.12 dependencies, and runtime settings for unbuffered, bytecode-free execution.
- Copied `src/`, `alembic/`, and `alembic.ini` before installing the API and worker extras so setuptools package discovery sees the source tree.
- Created uid/gid `10001`, made `/app/outputs` and `/app/outputs/datasets` writable, exposed port `8000`, and set the factory-based Uvicorn default command.
- Added a Node 22 frontend build stage using `npm ci`, with only public `VITE_API_BASE_URL` accepted and `/api` as the default.
- Limited frontend build copies to package metadata, build configuration, `src/`, and `public/`, so local `.env` files and backend secrets are not copied into the image.
- Added an Nginx 1.27 Alpine runtime listening on port `8080`, serving `/healthz` with `200 ok`, and using SPA fallback routing.
- Added deployment contract tests for image bases, dependency installation, public runtime configuration, secret handling, and Docker ignore rules.

## TDD evidence

The new deployment tests were added before the implementation files. The first run failed with two missing-file failures. After implementation, one run exposed the required literal `.env.compose` entry; adding it made the contract pass.

## Verification

- `.\\.venv\\Scripts\\python.exe -m pytest tests/deployment/test_contract.py -q`: `2 passed`.
- `npm run typecheck`: passed.
- `npm run test`: `12` test files and `30` tests passed.
- `npm run build`: passed; Vite produced the production bundle.

## Concerns

- `npm ci` reported four moderate audit findings in the existing frontend dependency graph; no dependency upgrades were made within this task.
- Docker image builds were not run because the brief required static deployment tests and frontend checks; Dockerfile contract checks passed.
