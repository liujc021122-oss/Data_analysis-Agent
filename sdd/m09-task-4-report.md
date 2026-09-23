# M09 Task 4 Report: Backend Selection and Production Fail-Closed Configuration

## Status

Task 4 adds typed execution-selection settings and a backend factory. The
development and test profiles use the local compatibility backend by default;
production is forced to the container backend and cannot select local IPython
execution. The image requirement is enforced at backend construction so
database/storage-only production configuration tests remain independent of
execution startup.

## Changes

- Added `execution_backend`, `execution_image`, and
  `execution_network_mode` to `Settings`.
- Added `EXECUTION_BACKEND`, `EXECUTION_IMAGE`, and network-mode/policy
  parsing with explicit key-named configuration errors.
- Production settings normalize the backend to `container` by default and
  reject an explicit local backend.
- Added `build_execution_backend(settings, runtime=...)` and exported it from
  both `data_analysis_agent.execution` and the top-level package.
- The factory returns `LocalCodeExecutor` only for development/test and
  returns `ContainerCodeExecutor` for production/container settings. Missing
  container images fail clearly; runtime unavailability remains a stable
  `BACKEND_UNAVAILABLE` result with no local fallback.
- Production dotenv files remain isolated from test/development settings.

## TDD evidence

### RED

Focused command before the factory/settings implementation:

```powershell
$env:PYTHONPATH = '.;src'
pytest -q tests/execution/test_backend_factory.py
```

Result: collection failed because `build_execution_backend` was not yet
exported. The failure identified the missing factory boundary rather than a
real Docker/model dependency.

### GREEN

Focused configuration/factory/packaging command:

```powershell
$env:PYTHONPATH = '.;src'
pytest -q tests/config tests/execution/test_backend_factory.py tests/packaging/test_package_contract.py
```

Result: **33 passed, 5 warnings in 33.39s**.

The warnings are existing Pydantic protected-namespace warnings. No real
model API, Docker daemon, or network was used.

## Scope boundary

This task does not yet replace the legacy Agent's direct `CodeExecutor`
construction. That integration belongs to Task 5, where settings-derived
limits and network policy will be converted into execution requests.

## Report path

`E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.worktrees\m09-secure-execution\sdd\m09-task-4-report.md`
