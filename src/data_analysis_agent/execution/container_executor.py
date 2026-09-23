from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from .backend import CodeExecutionBackend
from .code_executor import LocalCodeExecutor
from .errors import ExecutionErrorCode, sanitize_execution_text
from .models import ExecutionRequest, ExecutionResult, NetworkPolicy
from .runtime import (
    ContainerMount,
    ContainerRuntime,
    ContainerRuntimeError,
    ContainerSpec,
    DockerCliRuntime,
    RuntimeOutput,
    RuntimeTimeoutError,
    RuntimeWaitResult,
)


class ContainerCodeExecutor:
    """Execute one request in a fresh, restricted container.

    This class deliberately has no local-executor fallback. Runtime failures
    become typed failed results so the caller can fail the task closed.
    """

    production_safe = True
    _SAFE_ENV = {
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
    }
    _COMMAND = (
        "python",
        "-c",
        "import sys; exec(compile(sys.stdin.read(), '<analysis-code>', 'exec'))",
    )

    def __init__(
        self,
        image: str,
        *,
        runtime: ContainerRuntime | None = None,
        user: str = "65532:65532",
    ) -> None:
        if not image or not image.strip():
            raise ValueError("container image must not be blank")
        if not self._is_non_root_user(user):
            raise ValueError("container executor requires a non-root user")
        self.image = image.strip()
        self.user = user.strip()
        self.runtime = runtime or DockerCliRuntime()

    @staticmethod
    def _is_non_root_user(user: str) -> bool:
        if not user or not user.strip():
            return False
        for identity in (part.strip().casefold() for part in user.strip().split(":")):
            if identity == "root":
                return False
            try:
                if int(identity, 10) == 0:
                    return False
            except ValueError:
                continue
        return True

    @staticmethod
    def _network_mode(policy: NetworkPolicy) -> str:
        return "none" if policy is NetworkPolicy.DISABLED else "bridge"

    @classmethod
    def _build_spec(
        cls,
        request: ExecutionRequest,
        *,
        image: str,
        user: str,
        input_dir: Path,
    ) -> ContainerSpec:
        return ContainerSpec(
            image=image,
            command=cls._COMMAND,
            mounts=(
                ContainerMount(
                    host_path=input_dir,
                    container_path="/input",
                    read_only=True,
                ),
                ContainerMount(
                    host_path=request.output_dir,
                    container_path="/output",
                    read_only=False,
                ),
            ),
            env=dict(cls._SAFE_ENV),
            network_mode=cls._network_mode(request.network_policy),
            user=user,
            read_only_rootfs=True,
            tmpfs=("/tmp",),
            cap_drop=("ALL",),
            security_options=("no-new-privileges",),
            pids_limit=request.limits.pids_limit,
            cpu_limit=request.limits.cpu_limit,
            memory_limit_bytes=request.limits.memory_limit_bytes,
            workdir="/output",
            max_output_bytes=request.limits.max_output_bytes,
        )

    @staticmethod
    def _stage_inputs(request: ExecutionRequest, input_dir: Path) -> None:
        for item in request.input_files:
            source = item.resolved_source_path.resolve(strict=False)
            scope = item.source_scope.resolve(strict=False)
            try:
                source.relative_to(scope)
            except ValueError as exc:
                raise ValueError("input path is outside the allowed scope") from exc
            if not source.is_file():
                raise FileNotFoundError("input file is not available")
            destination = input_dir.joinpath(*item.logical_name.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    @staticmethod
    def _failure(
        request: ExecutionRequest,
        *,
        code: ExecutionErrorCode,
        message: str,
        duration_ms: float,
        exit_code: int | None = None,
        timed_out: bool = False,
        resource_limited: bool = False,
        stdout: str = "",
        stderr: str = "",
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            resource_limited=resource_limited,
            error_code=code,
            error_message=sanitize_execution_text(message, secrets=(request.code,)),
            redaction_secrets=(request.code,),
            code_sha256=request.code_sha256,
            duration_ms=max(0.0, duration_ms),
        )

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started = time.perf_counter()
        container_id: str | None = None
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        error_code: ExecutionErrorCode | None = None
        error_message: str | None = None
        timed_out = False
        resource_limited = False
        output_files = ()
        cleanup_error = False

        try:
            if not self.runtime.available():
                return self._failure(
                    request,
                    code=ExecutionErrorCode.BACKEND_UNAVAILABLE,
                    message="container runtime is unavailable",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
        except Exception:
            return self._failure(
                request,
                code=ExecutionErrorCode.BACKEND_UNAVAILABLE,
                message="container runtime is unavailable",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="analysis-input-") as temporary_input:
                input_dir = Path(temporary_input).resolve()
                self._stage_inputs(request, input_dir)
                spec = self._build_spec(
                    request,
                    image=self.image,
                    user=self.user,
                    input_dir=input_dir,
                )
                try:
                    container_id = self.runtime.create(spec)
                    if not container_id:
                        raise ContainerRuntimeError("container id was empty")
                    try:
                        self.runtime.start(container_id, stdin=request.code)
                    except Exception:
                        error_code = ExecutionErrorCode.CONTAINER_FAILURE
                        error_message = "container failed to start"
                        try:
                            self.runtime.wait(container_id, timeout_seconds=0.5)
                        except Exception:
                            pass
                    else:
                        try:
                            wait_result = self.runtime.wait(
                                container_id,
                                timeout_seconds=request.limits.timeout_seconds,
                            )
                        except (RuntimeTimeoutError, TimeoutError):
                            timed_out = True
                            error_code = ExecutionErrorCode.TIMEOUT
                            error_message = "container execution timed out"
                            try:
                                self.runtime.kill(container_id)
                            except Exception:
                                pass
                            try:
                                wait_result = self.runtime.wait(
                                    container_id, timeout_seconds=5.0
                                )
                            except Exception:
                                wait_result = RuntimeWaitResult(
                                    exit_code=137, timed_out=True
                                )
                        except Exception:
                            wait_result = None
                            error_code = ExecutionErrorCode.CONTAINER_FAILURE
                            error_message = "container wait failed"

                        if wait_result is not None:
                            if wait_result.timed_out and not timed_out:
                                timed_out = True
                                error_code = ExecutionErrorCode.TIMEOUT
                                error_message = "container execution timed out"
                                try:
                                    self.runtime.kill(container_id)
                                except Exception:
                                    pass
                                try:
                                    wait_result = self.runtime.wait(
                                        container_id, timeout_seconds=5.0
                                    )
                                except Exception:
                                    pass
                            exit_code = wait_result.exit_code
                            resource_limited = wait_result.resource_limited
                            if resource_limited and not timed_out:
                                error_code = ExecutionErrorCode.RESOURCE_LIMIT
                                error_message = "container resource limit reached"
                            if (
                                wait_result.output_limited
                                and not timed_out
                                and not resource_limited
                            ):
                                error_code = ExecutionErrorCode.OUTPUT_LIMIT
                                error_message = "execution output exceeded configured limit"
                            if exit_code != 0 and not timed_out and not resource_limited:
                                if error_code is None:
                                    error_code = ExecutionErrorCode.CONTAINER_FAILURE
                                    error_message = "container exited unsuccessfully"
                            try:
                                output = self.runtime.read_output(
                                    container_id,
                                    max_output_bytes=request.limits.max_output_bytes,
                                )
                                if not isinstance(output, RuntimeOutput):
                                    raise ContainerRuntimeError("invalid runtime output")
                                stdout, stderr, output_limited = LocalCodeExecutor._bound_streams(
                                    output.stdout,
                                    output.stderr,
                                    request.limits.max_output_bytes,
                                )
                                output_limited = output_limited or output.truncated
                                if output_limited and not timed_out and not resource_limited:
                                    error_code = ExecutionErrorCode.OUTPUT_LIMIT
                                    error_message = "execution output exceeded configured limit"
                            except Exception:
                                error_code = ExecutionErrorCode.CONTAINER_FAILURE
                                error_message = "container output could not be collected"
                            if not timed_out and not resource_limited:
                                output_files, file_error_code, file_error_message = (
                                    LocalCodeExecutor._collect_output_files(request)
                                )
                                if file_error_code is not None:
                                    error_code = file_error_code
                                    error_message = file_error_message
                except Exception:
                    error_code = ExecutionErrorCode.CONTAINER_FAILURE
                    error_message = "container could not be created"
        except ValueError:
            error_code = ExecutionErrorCode.PATH_TRAVERSAL
            error_message = "input path is outside the allowed scope"
        except (OSError, FileNotFoundError):
            error_code = ExecutionErrorCode.CONTAINER_FAILURE
            error_message = "container input staging failed"
        finally:
            if container_id is not None:
                try:
                    self.runtime.remove(container_id)
                except Exception:
                    cleanup_error = True

        duration_ms = (time.perf_counter() - started) * 1000
        if cleanup_error:
            return self._failure(
                request,
                code=ExecutionErrorCode.CLEANUP_FAILURE,
                message="container cleanup failed",
                duration_ms=duration_ms,
                exit_code=exit_code,
                timed_out=timed_out,
                resource_limited=resource_limited,
                stdout=stdout,
                stderr=stderr,
            )
        if error_code is not None:
            return self._failure(
                request,
                code=error_code,
                message=error_message or "container execution failed",
                duration_ms=duration_ms,
                exit_code=exit_code,
                timed_out=timed_out,
                resource_limited=resource_limited,
                stdout=stdout,
                stderr=stderr,
            )
        return ExecutionResult(
            success=True,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code or 0,
            code_sha256=request.code_sha256,
            duration_ms=duration_ms,
            output_files=output_files,
            redaction_secrets=(request.code,),
        )


__all__ = ["ContainerCodeExecutor"]
