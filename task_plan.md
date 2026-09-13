# M02 Task 6 Plan

## Goal

Verify the domain, persistence, and API model boundaries; preserve M00/M01 behavior; record and commit the complete no-key regression evidence.

## Phases

- [x] Write and run the cross-layer tests (TDD RED/GREEN evidence)
- [x] Run compile, full no-key regression, help, dependency, diff, and external-import checks
- [x] Update progress, findings, and task report with exact evidence
- [x] Commit Task 6 changes and verify the final commit

## Evidence

- Cross-layer contract tests: `2 passed in 3.45s`.
- Full no-key regression: `162 passed` after final domain snapshot hardening.
- Compileall, module help, root help, pip check, diff check, and external import verification exited 0.
- The specified external command first failed with exit `9009` because `Get-Command python` resolved to the WindowsApps alias; the safe explicit-venv interpreter rerun passed.
- No Agent/Worker/API runtime orchestration was added or changed.
- Final hardening commits: `da11b9f`, `22e2275`, `f66e235`, and `cc384f7` close immutable snapshot, canonical JSON, UTC, cycle/key, and stable-set-sorting boundaries.

## Constraints

- Do not change M00/M01 runtime orchestration.
- Do not add API keys, virtual environments, temporary external directories, or generated reports to Git.
- Preserve the existing SDD ledger at `.git/worktrees/m02-domain-models/sdd/progress.md`.

## M03 Final Review Fix: ordered task-dataset associations

### Goal

Persist request tuple order for task datasets across creation, reload, restart, and idempotent lookup while preserving ownership, foreign keys, duplicate protection, and order-sensitive request hashing.

### Phases

- [x] Add order regression and migration assertions; run RED.
- [x] Implement ORM/repository/service ordering and reversible migration; run GREEN.
- [x] Run focused/full verification and SQLite migration lifecycle checks.
- [x] Update SDD report, self-review, and commit all changes.

### Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| PowerShell smoke wrapper | 1 | Simplified to direct explicit-venv Python invocation; smoke passed |
