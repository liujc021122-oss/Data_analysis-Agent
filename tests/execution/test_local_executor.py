from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.execution import (
    CodeExecutor,
    ExecutionErrorCode,
    ExecutionLimits,
    ExecutionRequest,
    LocalCodeExecutor,
)


def make_request(tmp_path: Path, code: str, *, limits: ExecutionLimits | None = None):
    output_scope = tmp_path / "outputs"
    output_dir = output_scope / "task"
    output_dir.mkdir(parents=True)
    return ExecutionRequest(
        task_id=uuid4(),
        code=code,
        output_dir=output_dir,
        output_scope=output_scope,
        limits=limits or ExecutionLimits(),
    )


def test_local_backend_returns_typed_success_with_code_hash_and_duration(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(tmp_path, "print('local execution')")

    result = executor.execute(request)

    assert result.success is True
    assert result.exit_code == 0
    assert result.error_code is None
    assert "local execution" in result.stdout
    assert result.code_sha256 == request.code_sha256
    assert result.duration_ms >= 0


def test_local_backend_maps_execution_failure_without_unbounded_traceback(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(tmp_path, "raise RuntimeError('planned local failure')")

    result = executor.execute(request)

    assert result.success is False
    assert result.exit_code != 0
    assert result.error_code is ExecutionErrorCode.EXECUTION_FAILED
    assert "planned local failure" in result.error_message
    assert len(result.error_message) <= 16_384


def test_local_backend_collects_bounded_output_file_metadata(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "from pathlib import Path\n"
        "Path(session_output_dir, 'chart.png').write_bytes(b'png-bytes')\n"
        "print('done')",
    )

    result = executor.execute(request)

    assert result.success is True
    assert [item.logical_name for item in result.output_files] == ["chart.png"]
    artifact = result.output_files[0]
    assert artifact.size_bytes == len(b"png-bytes")
    assert artifact.sha256 == sha256(b"png-bytes").hexdigest()
    assert artifact.suffix == ".png"
    assert result.model_dump_json()


def test_local_backend_marks_stdout_limit_and_bounds_feedback(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "print('x' * 400)",
        limits=ExecutionLimits(max_output_bytes=64),
    )

    result = executor.execute(request)

    assert result.success is False
    assert result.error_code is ExecutionErrorCode.OUTPUT_LIMIT
    assert len(result.stdout.encode("utf-8")) <= 64
    assert "output" in result.error_message.lower()


def test_legacy_code_executor_preserves_execute_code_and_set_variable(tmp_path):
    executor = CodeExecutor(tmp_path / "legacy")
    executor.set_variable("answer", 42)

    result = executor.execute_code("print(answer)")

    assert result["success"] is True
    assert result["output"].strip() == "42"
    assert result["error"] == ""
    assert isinstance(executor, LocalCodeExecutor)


def test_local_executor_uses_request_output_dir_for_chart_compatibility(tmp_path):
    executor = CodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "import matplotlib.pyplot as plt\n"
        "plt.figure()\n"
        "plt.plot([1, 2], [2, 1])\n"
        "plt.savefig(Path(session_output_dir, 'trend.png'))\n"
        "plt.close()",
    )
    executor.set_variable("Path", Path)

    result = executor.execute(request)

    assert result.success is True
    assert (request.output_dir / "trend.png").exists()
    assert result.output_files[0].logical_name == "trend.png"


def test_legacy_reset_does_not_close_figures_owned_by_another_executor(tmp_path):
    first = CodeExecutor(tmp_path / "first")
    second = CodeExecutor(tmp_path / "second")

    first.set_variable("value", 1)
    second.set_variable("value", 2)
    second.reset_environment()

    assert first.execute_code("print(value)")["output"].strip() == "1"
    assert second.execute_code("print('value' in globals())")["output"].strip() == "False"
