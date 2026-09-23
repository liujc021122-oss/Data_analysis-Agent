# Task 3: 容器运行时协议与 `ContainerCodeExecutor`

## Context

Task 1 established typed execution contracts and Task 2 established the bounded development/test-only local backend. Production must now execute untrusted code in a fresh, restricted container. The runtime must be injectable so tests use a fake runtime and do not require Docker.

## Requirements

- Define a synchronous `ContainerRuntime` protocol/adapter boundary with operations sufficient to create/start a one-shot container, stream or collect bounded stdout/stderr, inspect exit status, kill, wait, and remove it.
- Implement `ContainerCodeExecutor` using `CodeExecutionBackend.execute(request) -> ExecutionResult`.
- Every request gets isolated staging/container state. Inputs are mounted read-only at a fixed container path; outputs are the only writable host bind mount at a fixed container path. Do not mount project source, Docker socket, host root, or arbitrary host paths.
- Pass security/resource settings to the runtime: non-root user, network `none` for `NetworkPolicy.DISABLED`, read-only root filesystem, tmpfs `/tmp`, drop all capabilities, `no-new-privileges`, pids limit, CPU limit, memory limit, and execution deadline.
- Do not inherit the host environment. Pass only a minimal explicit environment allowlist; never pass API keys, database/Redis URLs, storage secrets, or the raw host output path.
- Use a controlled script/stdin protocol so model code sees fixed `/input` and `/output` paths rather than host paths. Preserve code SHA-256 and bounded/sanitized diagnostics.
- On timeout, kill then wait then remove. On normal completion, failure, startup error, and collection error, always attempt wait/remove; surface stable `TIMEOUT`, `RESOURCE_LIMIT`, `CONTAINER_FAILURE`, `BACKEND_UNAVAILABLE`, `PATH_TRAVERSAL`, `OUTPUT_LIMIT`, `FILE_LIMIT`, or `CLEANUP_FAILURE` codes as appropriate without leaking daemon details.
- Reuse Task 2's bounded output-file metadata checks, but do not call the local IPython executor or silently fall back to it.
- Add fake-runtime contract tests first (TDD) for security arguments, successful output collection, startup failure, timeout cleanup order, nonzero exit, network policy, environment filtering, and no fallback.
- Keep real Docker CLI integration optional and offline tests independent of Docker/model/network.

## Verification

- Capture RED before implementing runtime/backend.
- Run focused container tests and all existing local executor/Agent/integration regressions.
- Run the full offline suite if feasible and record exact pass/fail/skip output.
- Write `sdd/m09-task-3-report.md`, self-review, and commit with `feat: M09 task 3 ...`.
- Do not implement configuration factory, production settings selection, or Agent wiring in this task; those are Task 4/5.
