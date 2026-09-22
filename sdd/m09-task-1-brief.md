# Task 1: 执行模型、限制策略和稳定错误契约

## Context

M09 的设计要求把不可信代码执行抽象为统一执行端口。当前分支基于 M08；现有 `src/data_analysis_agent/execution/code_executor.py` 是进程内 IPython 兼容实现，后续任务会在它之上实现 local/container 后端。本任务只建立领域模型、校验和稳定错误契约，不实现执行后端或 Agent 接入。

## Requirements

- Add typed execution-domain models for:
  - `NetworkPolicy`: `DISABLED` and `ENABLED`, with disabled as the default.
  - `ExecutionLimits`: positive, bounded `timeout_seconds`, `memory_limit_bytes`, `cpu_limit`, `pids_limit`, `max_output_bytes`, and `max_files`.
  - `ExecutionInput`: logical file name, resolved source path, and read-only property; reject unsafe logical names and paths outside the allowed source scope.
  - `ExecutionRequest`: task ID, code, input files, resolved output directory, limits, and network policy.
  - `ExecutionFile`: logical name, size, SHA-256, and MIME/suffix metadata.
  - `ExecutionResult`: success, stdout/stderr, exit code, timeout/resource-limit flags, stable error code, code SHA-256, duration, and output file metadata.
- Use the project's existing Pydantic v2 conventions and make models JSON serializable.
- Define a `CodeExecutionBackend` protocol with `execute(request: ExecutionRequest) -> ExecutionResult`.
- Define stable execution error codes/exceptions for configuration missing, backend unavailable, timeout, resource limit, output limit, file limit, path traversal, network denied, container failure, and cleanup failure.
- Add path-scope validation and safe text truncation/sanitization. Error text must not expose raw code, API keys, environment values, full host paths, or unbounded output.
- Compute code SHA-256 deterministically without retaining raw code in audit/error data.
- Keep this task focused on contracts and validation. Do not modify the existing IPython behavior or production wiring yet.

## Global constraints

- Production must eventually be container-only and must never fall back to local execution; this task must not introduce a fallback.
- Tests must be offline and must not require Docker, a Docker daemon, a model API, or a network connection.
- Do not include raw code, secrets, absolute host paths, full Docker commands, or unbounded output in errors or audit-facing models.

## TDD and verification

1. Write focused tests first and run them to capture the expected RED failure.
2. Implement the smallest contract surface that makes the tests pass.
3. Run the focused tests, then the existing offline suite before committing.
4. Record RED/GREEN commands and output, changed files, and any concerns in `sdd/m09-task-1-report.md`.
5. Commit the implementation with a `feat: M09 task 1 ...` subject and report the commit.
