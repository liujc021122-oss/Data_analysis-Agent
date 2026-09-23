from __future__ import annotations

from dataclasses import dataclass, field
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


_DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_OUTPUT_READ_CHUNK_BYTES = 64 * 1024


class ContainerRuntimeError(RuntimeError):
    """A container runtime could not perform a requested operation."""


class RuntimeTimeoutError(TimeoutError, ContainerRuntimeError):
    """The runtime did not finish a container before the deadline."""


@dataclass(frozen=True)
class ContainerMount:
    host_path: Path
    container_path: str
    read_only: bool


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    command: tuple[str, ...]
    mounts: tuple[ContainerMount, ...]
    env: Mapping[str, str]
    network_mode: str
    user: str
    read_only_rootfs: bool
    tmpfs: tuple[str, ...]
    cap_drop: tuple[str, ...]
    security_options: tuple[str, ...]
    pids_limit: int
    cpu_limit: float
    memory_limit_bytes: int
    workdir: str = "/output"
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES


@dataclass(frozen=True)
class RuntimeWaitResult:
    exit_code: int
    timed_out: bool = False
    resource_limited: bool = False
    output_limited: bool = False


@dataclass(frozen=True)
class RuntimeOutput:
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False


@dataclass
class _OutputCapture:
    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    total_bytes: int = 0
    truncated: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _threads: tuple[threading.Thread, ...] = field(default=(), repr=False)

    def start(self, process: Any) -> None:
        streams = (
            (getattr(process, "stdout", None), self.stdout),
            (getattr(process, "stderr", None), self.stderr),
        )
        threads = []
        for stream, target in streams:
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain,
                args=(stream, target),
                daemon=True,
            )
            thread.start()
            threads.append(thread)
        self._threads = tuple(threads)

    def _drain(self, stream: Any, target: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(_OUTPUT_READ_CHUNK_BYTES)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                with self._lock:
                    remaining = max(0, self.limit - self.total_bytes)
                    if remaining:
                        accepted = chunk[:remaining]
                        target.extend(accepted)
                        self.total_bytes += len(accepted)
                    if len(chunk) > remaining:
                        self.truncated = True
        except (OSError, ValueError):
            return

    def join(self) -> None:
        for thread in self._threads:
            thread.join(timeout=1.0)

    def snapshot(self, max_output_bytes: int) -> RuntimeOutput:
        with self._lock:
            stdout = bytes(self.stdout)
            stderr = bytes(self.stderr)
            truncated = self.truncated
        limit = max(0, max_output_bytes)
        bounded_stdout = stdout[:limit]
        remaining = max(0, limit - len(bounded_stdout))
        bounded_stderr = stderr[:remaining]
        truncated = truncated or len(stdout) + len(stderr) > limit
        return RuntimeOutput(
            stdout=bounded_stdout.decode("utf-8", errors="replace"),
            stderr=bounded_stderr.decode("utf-8", errors="replace"),
            truncated=truncated,
        )


@runtime_checkable
class ContainerRuntime(Protocol):
    def available(self) -> bool:
        ...

    def create(self, spec: ContainerSpec) -> str:
        ...

    def start(self, container_id: str, *, stdin: str) -> None:
        ...

    def wait(self, container_id: str, *, timeout_seconds: float) -> RuntimeWaitResult:
        ...

    def read_output(self, container_id: str, *, max_output_bytes: int) -> RuntimeOutput:
        ...

    def kill(self, container_id: str) -> None:
        ...

    def remove(self, container_id: str) -> None:
        ...


class DockerCliRuntime:
    """Small Docker CLI adapter; container policy is supplied by ``ContainerSpec``."""

    def __init__(
        self,
        docker_binary: str = "docker",
        *,
        command_timeout_seconds: float = 10.0,
    ) -> None:
        if not math.isfinite(command_timeout_seconds) or command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive and finite")
        self.docker_binary = docker_binary
        self.command_timeout_seconds = command_timeout_seconds
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._captures: dict[str, _OutputCapture] = {}

    def available(self) -> bool:
        return shutil.which(self.docker_binary) is not None

    def create(self, spec: ContainerSpec) -> str:
        command = [self.docker_binary, "create"]
        command.extend(("--network", spec.network_mode, "--user", spec.user))
        if spec.read_only_rootfs:
            command.append("--read-only")
        command.extend(("--workdir", spec.workdir))
        for tmpfs_path in spec.tmpfs:
            command.extend(("--tmpfs", f"{tmpfs_path}:rw,noexec,nosuid,size=64m"))
        for capability in spec.cap_drop:
            command.extend(("--cap-drop", capability))
        for option in spec.security_options:
            command.extend(("--security-opt", option))
        command.extend(("--pids-limit", str(spec.pids_limit)))
        command.extend(("--cpus", str(spec.cpu_limit)))
        command.extend(("--memory", str(spec.memory_limit_bytes)))
        for mount in spec.mounts:
            mode = "ro" if mount.read_only else "rw"
            command.extend(
                (
                    "--mount",
                    f"type=bind,src={mount.host_path},dst={mount.container_path},{mode}",
                )
            )
        for name, value in sorted(spec.env.items()):
            command.extend(("--env", f"{name}={value}"))
        command.extend((spec.image, *spec.command))
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env={},
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ContainerRuntimeError("container create failed") from exc
        container_id = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
        if not container_id:
            raise ContainerRuntimeError("container create returned no id")
        self._captures[container_id] = _OutputCapture(
            max(1, spec.max_output_bytes)
        )
        return container_id

    def start(self, container_id: str, *, stdin: str) -> None:
        try:
            process = subprocess.Popen(
                [self.docker_binary, "start", "--attach", "--interactive", container_id],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                env={},
            )
        except OSError as exc:
            raise ContainerRuntimeError("container start failed") from exc
        self._processes[container_id] = process
        capture = self._captures.setdefault(
            container_id, _OutputCapture(_DEFAULT_MAX_OUTPUT_BYTES)
        )
        capture.start(process)
        if process.stdin is None:
            raise ContainerRuntimeError("container stdin unavailable")
        try:
            process.stdin.write(stdin.encode("utf-8"))
            process.stdin.close()
        except (OSError, TypeError) as exc:
            raise ContainerRuntimeError("container stdin failed") from exc

    def wait(self, container_id: str, *, timeout_seconds: float) -> RuntimeWaitResult:
        process = self._processes.get(container_id)
        if process is None:
            raise ContainerRuntimeError("container process unavailable")
        capture = self._captures.get(container_id)
        if capture is None:
            raise ContainerRuntimeError("container output capture unavailable")
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                return_code = process.poll()
                if return_code is not None:
                    break
                if capture.truncated:
                    self._terminate_for_output(container_id, process)
                    try:
                        process.wait(timeout=self.command_timeout_seconds)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeTimeoutError(
                            "container could not stop after output limit"
                        ) from exc
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeTimeoutError("container execution timed out")
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is not None:
                capture.join()
        return RuntimeWaitResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            output_limited=capture.truncated,
        )

    def read_output(self, container_id: str, *, max_output_bytes: int) -> RuntimeOutput:
        capture = self._captures.get(container_id)
        if capture is None:
            return RuntimeOutput()
        return capture.snapshot(max_output_bytes)

    def _terminate_for_output(
        self,
        container_id: str,
        process: subprocess.Popen[str],
    ) -> None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            subprocess.run(
                [self.docker_binary, "kill", container_id],
                check=False,
                capture_output=True,
                text=True,
                env={},
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def kill(self, container_id: str) -> None:
        process = self._processes.get(container_id)
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            subprocess.run(
                [self.docker_binary, "kill", container_id],
                check=False,
                capture_output=True,
                text=True,
                env={},
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerRuntimeError("container kill failed") from exc

    def remove(self, container_id: str) -> None:
        try:
            completed = subprocess.run(
                [self.docker_binary, "rm", "-f", container_id],
                check=False,
                capture_output=True,
                text=True,
                env={},
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerRuntimeError("container remove failed") from exc
        finally:
            self._processes.pop(container_id, None)
            self._captures.pop(container_id, None)
        if completed.returncode != 0:
            raise ContainerRuntimeError("container remove failed")


__all__ = [
    "ContainerMount",
    "ContainerRuntime",
    "ContainerRuntimeError",
    "ContainerSpec",
    "DockerCliRuntime",
    "RuntimeOutput",
    "RuntimeTimeoutError",
    "RuntimeWaitResult",
]
