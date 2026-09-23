from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable


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


@dataclass(frozen=True)
class RuntimeWaitResult:
    exit_code: int
    timed_out: bool = False
    resource_limited: bool = False


@dataclass(frozen=True)
class RuntimeOutput:
    stdout: str = ""
    stderr: str = ""


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

    def __init__(self, docker_binary: str = "docker") -> None:
        self.docker_binary = docker_binary
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._outputs: dict[str, RuntimeOutput] = {}

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
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ContainerRuntimeError("container create failed") from exc
        container_id = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
        if not container_id:
            raise ContainerRuntimeError("container create returned no id")
        return container_id

    def start(self, container_id: str, *, stdin: str) -> None:
        try:
            process = subprocess.Popen(
                [self.docker_binary, "start", "--attach", "--interactive", container_id],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={},
            )
        except OSError as exc:
            raise ContainerRuntimeError("container start failed") from exc
        self._processes[container_id] = process
        if process.stdin is None:
            raise ContainerRuntimeError("container stdin unavailable")
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except OSError as exc:
            raise ContainerRuntimeError("container stdin failed") from exc

    def wait(self, container_id: str, *, timeout_seconds: float) -> RuntimeWaitResult:
        process = self._processes.get(container_id)
        if process is None:
            raise ContainerRuntimeError("container process unavailable")
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeTimeoutError("container execution timed out") from exc
        self._outputs[container_id] = RuntimeOutput(stdout=stdout or "", stderr=stderr or "")
        return RuntimeWaitResult(exit_code=process.returncode or 0)

    def read_output(self, container_id: str, *, max_output_bytes: int) -> RuntimeOutput:
        del max_output_bytes
        return self._outputs.get(container_id, RuntimeOutput())

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
            )
        except OSError as exc:
            raise ContainerRuntimeError("container kill failed") from exc

    def remove(self, container_id: str) -> None:
        try:
            completed = subprocess.run(
                [self.docker_binary, "rm", "-f", container_id],
                check=False,
                capture_output=True,
                text=True,
                env={},
            )
        except OSError as exc:
            raise ContainerRuntimeError("container remove failed") from exc
        finally:
            self._processes.pop(container_id, None)
            self._outputs.pop(container_id, None)
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
