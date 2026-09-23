# Task 2: 本地后端与旧 `CodeExecutor` 兼容门面

## Context

Task 1 provides the typed execution contracts in `data_analysis_agent.execution`. The existing `CodeExecutor` is an IPython-based development implementation used by M00-M08 compatibility tests and by the legacy agent. M09 must keep that development/test behavior while routing new code through the execution port. This task does not implement Docker or production selection.

## Requirements

- Add `LocalCodeExecutor` implementing `CodeExecutionBackend.execute(request: ExecutionRequest) -> ExecutionResult`.
- Reuse the existing IPython behavior where compatibility requires it, but return the new result contract with code SHA-256, duration, exit/error information, bounded stdout/stderr, and output file metadata.
- Enforce request input/output path validation from Task 1 and local output/file/byte limits; do not expose unbounded diagnostic text.
- Preserve development/test compatibility for the legacy `CodeExecutor.execute_code(code) -> dict` shape, `set_variable`, `reset_environment`, environment information, and existing chart/report flows.
- Keep the compatibility facade clearly non-production; it must not become a production fallback or select itself based on model output.
- Keep executor instances isolated and ensure cleanup/reset does not close figures or state owned by another executor.
- Add focused tests first (TDD) covering successful execution, failed execution, bounded result mapping, output metadata, and legacy compatibility. Tests must not require a model, Docker, network, or API key.
- Do not change configuration/factory or Agent wiring yet; those are later tasks.

## Verification

- Watch the focused tests fail before implementation.
- Run focused local execution tests, existing executor contract/integration tests, and the offline suite with `PYTHONPATH=.;src`.
- Write the TDD evidence and self-review to `sdd/m09-task-2-report.md`, then commit with a `feat: M09 task 2 ...` subject.
