# M09 Task 2 Report: Local Backend and Compatibility Facade

## Status

Complete for the four Task 2 review findings. The local backend remains
development/test-only. No Docker backend, configuration/factory wiring, or
Agent wiring was changed.

## Fixes

- Typed execution now supplies IPython with a shared UTF-8 byte-budget writer
  for stdout and stderr. It retains at most `request.limits.max_output_bytes`
  and stops typed execution when the budget is exceeded. The legacy
  `execute_code()` path keeps its existing capture behavior and compatibility
  shape.
- Output traversal validates resolved paths, including symlinks, before any
  file metadata read. It checks `max_files` and cumulative file bytes before
  calling metadata hashing for the next file.
- Result duration is measured after output-file collection and hashing.
- The isolation regression creates a real matplotlib figure in the first
  executor, resets the second executor, verifies the first figure remains
  alive, then resets and closes the owned figure during cleanup.

Local execution remains in-process IPython. Its timeout behavior is explicitly
post-execution/cooperative classification; this task does not claim hard
process termination.

## TDD evidence

### RED

Command:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest tests/execution/test_local_executor.py -q -k "stops_code_when_output_limit_is_reached or output_collection_stops_before_hashing or output_collection_rejects_escaped_symlink_before_hashing or measures_duration_after_output_collection or reset_does_not_close_figures_owned_by_another_executor"
```

Result: **4 failed, 1 passed, 1 skipped, 7 deselected, 5 warnings in 6.94s**.
The failures demonstrated post-capture execution, hashing beyond the file
count limit, missing cumulative file-byte enforcement, and duration measured
before collection. The symlink regression was skipped because symlink creation
was unavailable in this Windows environment.

### GREEN

Command:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest tests/execution/test_local_executor.py -q
```

Result: **12 passed, 1 skipped, 14 warnings in 8.58s**.

## Regression verification

Command:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest tests/contract/test_executor_contract.py tests/contract/test_executor_privacy.py tests/tools/test_executor.py tests/agent/test_agent_compatibility.py tests/integration/test_analysis_flow.py tests/integration/test_dataset_analysis_flow.py tests/llm/test_agent_structured_boundary.py -q
```

Result: **64 passed, 5 warnings in 9.36s**.

Full offline-suite attempt:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest -q
```

The run was terminated after visible progress reached approximately 70%.
The visible output included `s.sF` and therefore at least one failure, but
pytest produced no final summary or reliable exit code. The full suite is
recorded as incomplete, not passing.

## Concerns

- The symlink regression remains environment-skipped when symlink creation is
  unavailable.
- Existing Pydantic protected-namespace and IPython deprecation warnings
  remain unchanged.
- Production isolation and hard timeout/resource enforcement remain outside
  this task and belong to the later container backend/configuration work.

## Report path

`E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.worktrees\m09-secure-execution\sdd\m09-task-2-report.md`
