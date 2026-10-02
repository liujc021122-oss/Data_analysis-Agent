# M20 Task 10 Fix Report

## Root cause

`src/data_analysis_agent/storage/factory.py` accessed the newly explicit storage fields directly. Legacy CLI test settings are `SimpleNamespace` objects that provide `app_env` and `storage_local_root`, but do not provide `storage_backend`, `storage_signing_secret`, `storage_endpoint`, or `storage_bucket`. The first direct access raised `AttributeError`, so production dataset mode did not reach the existing fail-closed `ConfigurationError` path.

## RED evidence

The focused regression test was added before changing production code:

```text
.\\.venv\\Scripts\\python.exe -m pytest tests/storage/test_factory.py -k legacy -q
FFF                                                                      [100%]
...
E       AttributeError: 'types.SimpleNamespace' object has no attribute 'storage_signing_secret'
...
3 failed, 12 deselected in 5.14s
```

The two existing CLI regressions also failed before the fix:

```text
.\\.venv\\Scripts\\python.exe -m pytest tests/test_cli_dataset_ids.py -q
......F.F                                                                [100%]
...
FAILED tests/test_cli_dataset_ids.py::test_build_dataset_resolver_rejects_production_local_storage
...
E       AttributeError: 'types.SimpleNamespace' object has no attribute 'storage_signing_secret'
...
FAILED tests/test_cli_dataset_ids.py::test_production_cli_dataset_id_fails_closed_before_database_creation
E       assert 1 == 2
2 failed, 7 passed in 5.00s
```

## Fix and GREEN evidence

At the compatibility boundary, `getattr` now supplies these defaults:

- missing signing secret: `None`
- missing backend: `s3` for production and `local` otherwise
- missing endpoint or bucket: `None`, allowing the existing S3 configuration error

Explicit `Settings.storage_backend` remains authoritative. Existing signing-secret encoding and S3 credential handling remain unchanged. The missing-object-storage message includes “object storage” to preserve the existing CLI assertion.

Focused post-fix checks:

```text
.\\.venv\\Scripts\\python.exe -m pytest tests/storage/test_factory.py -k legacy -q
...                                                                      [100%]
3 passed, 12 deselected in 4.42s

.\\.venv\\Scripts\\python.exe -m pytest tests/test_cli_dataset_ids.py -q
.........                                                                [100%]
9 passed in 5.07s
```

Mandated covering checks:

```text
.\\.venv\\Scripts\\python.exe -m pytest tests/storage/test_factory.py tests/test_cli_dataset_ids.py -q
........................                                                 [100%]
24 passed in 4.01s

.\\.venv\\Scripts\\python.exe -m pytest tests/config/test_settings_contract.py tests/storage/test_factory.py tests/test_cli_dataset_ids.py -q
..................................................                       [100%]
50 passed in 4.80s

.\\.venv\\Scripts\\python.exe -m mypy --config-file mypy.ini src/data_analysis_agent/storage/factory.py
Success: no issues found in 1 source file
```

## Files changed

- `src/data_analysis_agent/storage/factory.py`: added compatibility defaults at the storage factory boundary and preserved explicit settings behavior.
- `tests/storage/test_factory.py`: added regression coverage for legacy development, test, and production settings objects.
- `sdd/m20-task-10-fix-report.md`: this report.

Existing M20 progress, findings, and report files were preserved.

## Concerns

The compatibility test covers legacy production settings with missing endpoint and bucket, which is the fail-closed path involved in the regression. A legacy production object that supplies both endpoint and bucket but also omits region or credential fields would still require those older S3 fields; that case is outside the established regression and was left unchanged to keep the fix minimal.
