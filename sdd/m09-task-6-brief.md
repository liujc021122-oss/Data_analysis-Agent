# Task 6: Security Regression, Execution Audit Metadata, and Documentation

## Requirements

- Keep command, subprocess, network, environment-secret, host-source, input-write,
  and output-path restrictions covered by fake-runtime and local contract tests.
- Expose only minimal execution audit metadata: task ID, backend, code SHA-256,
  timestamps, duration, exit status, timeout/resource flags, stable error code,
  and output-file metadata. Never include source code or host paths.
- Make production fail closed even when an Agent is constructed directly without
  an explicit `Settings` object; use environment settings and reject unsafe
  injected backends.
- Retry temporary input cleanup once and keep cleanup failures observable without
  exposing filesystem details.
- Update environment examples and README to distinguish production containers
  from development/test local compatibility execution.

## Verification

- Run focused audit/security/configuration tests first.
- Run the M09 execution/Agent/integration regression set.
- Run the full offline suite and record only the pre-existing baseline failures.
- Run `git diff --check` and perform an independent review before committing.
