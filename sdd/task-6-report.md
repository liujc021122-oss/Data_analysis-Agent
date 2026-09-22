# Task 6 completion report

## Scope

Validated the legacy analysis adapter and its compatibility fixtures. No Task 7
files were changed.

## Acceptance checks

- Execution failures remain ordinary analysis results and are added to the
  conversation as feedback for the next model step.
- `max_rounds=0` makes no analysis-model calls and still invokes final report
  generation exactly once.
- Report generation is guarded so the reporting handler runs once.
- Legacy result and report values are converted to JSON-safe values, including
  Pydantic/domain objects and non-finite values.
- Model/schema failures use stable cause codes without exposing raw exception
  details, credentials, or absolute paths.

## Verification

- `pytest --import-mode=prepend tests/agent/test_legacy_adapter.py tests/contract/test_agent_contract.py -q`
  - 13 passed
- `pytest --import-mode=prepend tests/agent -q`
  - 55 passed
- `git diff --check`
  - passed
