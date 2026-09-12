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
- Full no-key regression: `126 passed in 26.20s`.
- Compileall, module help, root help, pip check, diff check, and external import verification exited 0.
- The specified external command first failed with exit `9009` because `Get-Command python` resolved to the WindowsApps alias; the safe explicit-venv interpreter rerun passed.
- No Agent/Worker/API runtime orchestration was added or changed.

## Constraints

- Do not change M00/M01 runtime orchestration.
- Do not add API keys, virtual environments, temporary external directories, or generated reports to Git.
- Preserve the existing SDD ledger at `.git/worktrees/m02-domain-models/sdd/progress.md`.
