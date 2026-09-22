# M07 Task 5

## Implementation

- Added typed built-in tool input/output models and `build_builtin_registry` in `src/data_analysis_agent/tools/builtins.py`.
- Registered `generate_report`, `inspect_dataset`, `profile_dataset`, `run_python_analysis`, `run_sql`, `save_chart`, and `validate_metric`.
- Exported the built-in models and factory from `data_analysis_agent.tools`.
- Missing injected dependencies fail when the affected handler is called with `ToolDependencyError`.

## Verification

- `E:\anaconda\python.exe -m pytest tests/tools/test_builtins.py -q`: **8 passed**.
- `E:\anaconda\python.exe -m pytest tests/tools -q`: **43 passed, 4 errors**. The four errors are existing fixture-setup failures for `uow_factory` in `tests/tools/test_persistence_audit.py`; no test failure occurred in the implemented built-ins.
- `git diff --check`: passed.
