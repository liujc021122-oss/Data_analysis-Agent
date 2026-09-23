from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import pytest

from data_analysis_agent.execution import (
    ContainerCodeExecutor,
    ContainerMount,
    ContainerSpec,
    ExecutionErrorCode,
    ExecutionLimits,
    ExecutionRequest,
    NetworkPolicy,
    RuntimeOutput,
    RuntimeTimeoutError,
    RuntimeWaitResult,
)


def make_request(
    tmp_path: Path,
    code: str = "print('container')",
    *,
    network_policy: NetworkPolicy = NetworkPolicy.DISABLED,
    limits: ExecutionLimits | None = None,
) -> ExecutionRequest:
    input_root = tmp_path / "input"
    output_root = tmp_path / "outputs"
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    (input_root / "sample.csv").write_text("value\n1\n", encoding="utf-8")
    return ExecutionRequest(
        task_id=uuid4(),
        code=code,
        input_files=(
            {
                "logical_name": "sample.csv",
                "source_path": input_root / "sample.csv",
                "source_scope": input_root,
            },
        ),
        output_dir=output_root / "task",
        output_scope=output_root,
        limits=limits or ExecutionLimits(),
        network_policy=network_policy,
    )


@dataclass
class FakeRuntime:
    wait_result: RuntimeWaitResult = RuntimeWaitResult(exit_code=0)
    output: RuntimeOutput = RuntimeOutput(stdout="ok", stderr="")
    available_result: bool = True
    create_error: Exception | None = None
    start_error: Exception | None = None
    remove_error: Exception | None = None

    def __post_init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.spec: ContainerSpec | None = None
        self.stdin: str | None = None
        self.killed = False

    def available(self) -> bool:
        self.events.append(("available", None))
        return self.available_result

    def create(self, spec: ContainerSpec) -> str:
        self.events.append(("create", spec))
        self.spec = spec
        if self.create_error:
            raise self.create_error
        return "container-1"

    def start(self, container_id: str, *, stdin: str) -> None:
        self.events.append(("start", container_id))
        self.stdin = stdin
        if self.start_error:
            raise self.start_error
        assert self.spec is not None
        output_mount = next(
            mount for mount in self.spec.mounts if mount.container_path == "/output"
        )
        (output_mount.host_path / "chart.png").write_bytes(b"png")

    def wait(self, container_id: str, *, timeout_seconds: float) -> RuntimeWaitResult:
        self.events.append(("wait", container_id))
        if self.killed:
            return RuntimeWaitResult(exit_code=137)
        return self.wait_result

    def read_output(self, container_id: str, *, max_output_bytes: int) -> RuntimeOutput:
        self.events.append(("read_output", max_output_bytes))
        return self.output

    def kill(self, container_id: str) -> None:
        self.events.append(("kill", container_id))
        self.killed = True

    def remove(self, container_id: str) -> None:
        self.events.append(("remove", container_id))
        if self.remove_error:
            raise self.remove_error


def test_container_backend_passes_security_mount_and_environment_policy(tmp_path):
    runtime = FakeRuntime()
    request = make_request(tmp_path, code="print('fixed paths')")

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is True
    assert result.exit_code == 0
    assert result.output_files[0].logical_name == "chart.png"
    assert runtime.stdin == request.code
    assert runtime.spec is not None
    assert runtime.spec.network_mode == "none"
    assert runtime.spec.user != "0"
    assert runtime.spec.read_only_rootfs is True
    assert "/tmp" in runtime.spec.tmpfs
    assert runtime.spec.cap_drop == ("ALL",)
    assert "no-new-privileges" in runtime.spec.security_options
    assert runtime.spec.pids_limit == request.limits.pids_limit
    assert runtime.spec.cpu_limit == request.limits.cpu_limit
    assert runtime.spec.memory_limit_bytes == request.limits.memory_limit_bytes
    assert {mount.container_path for mount in runtime.spec.mounts} == {"/input", "/output"}
    assert next(m.read_only for m in runtime.spec.mounts if m.container_path == "/input")
    assert not next(m.read_only for m in runtime.spec.mounts if m.container_path == "/output")
    assert "OPENAI_API_KEY" not in runtime.spec.env
    assert all("C:\\" not in value for value in runtime.spec.env.values())


def test_container_backend_uses_bridge_only_when_network_is_explicitly_enabled(tmp_path):
    runtime = FakeRuntime()
    request = make_request(tmp_path, network_policy=NetworkPolicy.ENABLED)

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is True
    assert runtime.spec is not None
    assert runtime.spec.network_mode == "bridge"


def test_container_backend_cleans_up_after_timeout_in_kill_wait_remove_order(tmp_path):
    runtime = FakeRuntime()
    runtime.wait_result = RuntimeWaitResult(exit_code=137, timed_out=True)
    request = make_request(tmp_path, limits=ExecutionLimits(timeout_seconds=0.1))

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is False
    assert result.timed_out is True
    assert result.error_code is ExecutionErrorCode.TIMEOUT
    names = [name for name, _ in runtime.events]
    assert names.index("kill") < names.index("wait", names.index("kill")) < names.index("remove")


def test_container_backend_maps_start_and_nonzero_failures_without_local_fallback(tmp_path):
    runtime = FakeRuntime(start_error=RuntimeError("daemon secret host path"))
    request = make_request(tmp_path)

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is False
    assert result.error_code is ExecutionErrorCode.CONTAINER_FAILURE
    assert "daemon secret" not in (result.error_message or "")
    assert any(name == "remove" for name, _ in runtime.events)

    runtime = FakeRuntime(wait_result=RuntimeWaitResult(exit_code=17))
    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )
    assert result.success is False
    assert result.error_code is ExecutionErrorCode.CONTAINER_FAILURE


def test_container_backend_returns_backend_unavailable_without_falling_back(tmp_path):
    runtime = FakeRuntime(available_result=False)
    request = make_request(tmp_path)

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is False
    assert result.error_code is ExecutionErrorCode.BACKEND_UNAVAILABLE
    assert [name for name, _ in runtime.events] == ["available"]


def test_container_backend_maps_output_and_cleanup_limits(tmp_path):
    runtime = FakeRuntime(output=RuntimeOutput(stdout="x" * 200, stderr=""))
    request = make_request(
        tmp_path,
        limits=ExecutionLimits(max_output_bytes=64),
    )

    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        request
    )

    assert result.success is False
    assert result.error_code is ExecutionErrorCode.OUTPUT_LIMIT
    assert len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) <= 64
    assert any(name == "remove" for name, _ in runtime.events)

    runtime = FakeRuntime(remove_error=RuntimeError("docker socket secret"))
    result = ContainerCodeExecutor(image="analysis:offline", runtime=runtime).execute(
        make_request(tmp_path / "cleanup")
    )
    assert result.success is False
    assert result.error_code is ExecutionErrorCode.CLEANUP_FAILURE
    assert "docker socket" not in (result.error_message or "")


def test_timeout_exception_is_supported_by_fake_runtime_contract():
    assert issubclass(RuntimeTimeoutError, RuntimeError)
