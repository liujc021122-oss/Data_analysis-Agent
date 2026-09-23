# M09 Task 2 Report: Local Backend and Compatibility Facade

## Status

Complete for the six Task 2 review findings. This update addresses the two
remaining review findings. The local backend remains development/test-only.
No Docker backend, configuration/factory wiring, or Agent wiring was changed.

## Fixes

- Typed execution now supplies IPython with a shared UTF-8 byte-budget writer
  for stdout and stderr. It retains at most `request.limits.max_output_bytes`
  and stops typed execution when the budget is exceeded. The legacy
  `execute_code()` path keeps its existing capture behavior and compatibility
  shape.
- Output traversal uses a lazy depth-first `Path.iterdir()` iterator instead
  of materializing `sorted(root.rglob("*"))`. It validates resolved paths,
  including symlinks, before any file metadata read, and stops discovery as
  soon as `max_files`, cumulative output bytes, or path escape is known.
- Result duration is measured after output-file collection and hashing.
- Figure ownership is tracked by `matplotlib.figure.Figure` object identity in
  a `weakref.WeakSet`, so cleanup cannot close a later figure that reuses an
  earlier executor's numeric figure ID.
- The isolation regressions create real matplotlib figures, exercise reset
  isolation, and verify that reused figure numbers remain owned by the second
  executor until its own reset.

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

Result: **14 passed, 1 skipped, 14 warnings in 10.67s**.

### Remaining-review focused verification

The two remaining regression tests were added before the production edits.
The initial focused RED invocation was interrupted before pytest emitted a
summary, so no RED count is claimed for that interrupted process. The
post-fix focused command was:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest tests/execution/test_local_executor.py -q -k "does_not_materialize_candidates_after_byte_limit or reused_figure_number_owned_by_another_executor"
```

Result: **2 passed, 13 deselected, 5 warnings in 5.59s**.

## Regression verification

Command:

```powershell
$env:PYTHONPATH='.;src'
py -3.12 -m pytest tests/contract/test_executor_contract.py tests/contract/test_executor_privacy.py tests/tools/test_executor.py tests/agent/test_agent_compatibility.py tests/integration/test_analysis_flow.py tests/integration/test_dataset_analysis_flow.py tests/llm/test_agent_structured_boundary.py -q
```

Result: **64 passed, 5 warnings in 11.84s**.

## Self-review

- The implementation no longer contains `sorted(root.rglob("*"))` or the
  numeric `_owned_figure_numbers` ownership set.
- The traversal test proves that an over-byte-limit first candidate does not
  consume a later candidate; existing tests still prove hashing stops before
  file-count and byte-limit violations and escaped symlinks are rejected
  before hashing.
- The figure-reuse test proves the first executor reset leaves the second
  executor's replacement figure alive, while the second reset closes it.
- The change set is limited to the local executor, its focused tests, and
  this report; Docker/configuration/Agent wiring was not changed.

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
