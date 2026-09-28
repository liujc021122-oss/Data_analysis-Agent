# M13 Subagent Execution Progress

- Task 1: complete (commits d84dda5..083a7a2; review clean after request-id, public-path, and metadata-scope fixes).
- Task 2: complete (commits c4f931f..0a53188; review clean after production-auth, validation-sanitization, and typed-container fixes).
- Task 3: complete (commits 4ff2c94..eed19c4; review clean after API coverage, error mapping, and persistence normalization fixes).
- Task 4: complete (implementation, independent review, two Important fixes, and re-review passed).
  - Added deterministic regression coverage for monotonic task-event timestamps and FrozenDict metadata filtering.
  - Added explicit downgrade protection for existing `FAILED -> QUEUED` retry events.
  - Focused API/worker/domain/database verification passed; Alembic head upgrade and empty-database downgrade passed.
- Task 5: complete (implementation, independent review, and fresh verification passed).
  - Added owner-scoped artifact metadata and authorized download endpoints.
  - Download URL issuance verifies storage object size and checksum; missing or mismatched objects use the stable not-found contract.
  - Artifact/storage and related API regression suites passed.
- Task 6: complete (integration review, documentation, independent review, and final verification passed).
  - Added complete OpenAPI path assertions, anonymous error-contract coverage, and public API export coverage.
  - Documented API installation/startup, local SQLite migration, development identity, production JWT/OIDC boundaries, upload, task submission, and API documentation endpoints.
  - Fixed README migration instructions to pass an explicit Alembic `db_url`; Alembic does not load `.env` automatically.
- Added the local protected Artifact `/content` endpoint and `content_url` contract during final integration review.

## Post-delivery audit repair (2026-09-28)

- Fixed the remaining report compatibility gap: each `ReportFormatResult` and the legacy Agent result now expose a direct `content_url` alongside the preserved `*_download_url` fields. Local signed tokens map to the protected Artifact `/content` endpoint; provider URLs remain unchanged.
- Fixed development startup without Redis: no `InMemoryTaskBroker` is injected, persistence remains available for inspection, and task submission/cancel/retry routes return `TASK_BROKER_NOT_CONFIGURED`. Test startup still injects `InMemoryTaskBroker`.

## Final Verification

- Focused API/Worker/domain/database suite: `211 passed`.
- Full suite: `901 passed, 1 skipped`; the only skip is the existing symlink-dependent execution test on this Windows environment.
- `compileall -q src`, editable installation, repeated Alembic upgrade to `20260927_0005`, Worker `--help`, and `git diff --check` passed.
- The final M12/M13 changes are intentionally integrated as one branch change set because the Worker, API, persistence, storage, configuration, tests, and documentation edits share files and runtime contracts.
