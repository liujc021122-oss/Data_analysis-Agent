# Task 5: Connect Agent and Task Execution to the Unified Backend

## Context

Task 3 defined typed execution requests/results and Task 4 defined
environment-based backend selection. The legacy Agent still constructs
`CodeExecutor` directly and expects stateful helpers such as
`set_variable()`/`execute_code()`. Production must pass through the typed
backend without falling back to in-process IPython, while development/test
must keep the compatibility facade used by existing callers.

## Requirements

- Add an Agent execution-session adapter over `CodeExecutionBackend`.
- Convert legacy code execution calls into `ExecutionRequest` values and map
  typed `ExecutionResult` values back to the established feedback shape.
- Expose only fixed `/input` and `/output` paths to container code; never put
  host output paths in the model-facing code protocol.
- Stage dataset-resolver data as controlled read-only inputs and expose a
  fixed-path `load_dataset()` helper inside the container script.
- Preserve enough successful code context for fresh container requests to
  continue multi-round analysis without shared container state.
- Use the production backend selected from `Settings`; development/test keeps
  the old `CodeExecutor` compatibility path unless an explicit backend is
  injected.
- Forward settings/runtime injection through `quick_analysis` for offline
  tests; production execution must not instantiate `CodeExecutor`.
- Clean temporary input staging after the task and preserve output artifacts.

## Verification

- Add focused tests first and capture RED.
- Test typed request construction, fixed paths, dataset staging, result mapping,
  and production Agent wiring with a fake backend.
- Run Agent/execution/integration regressions and the full offline suite.
- Write `sdd/m09-task-5-report.md` and commit the task separately.
