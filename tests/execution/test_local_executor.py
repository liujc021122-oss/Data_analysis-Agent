from hashlib import sha256
from pathlib import Path
import time
from uuid import uuid4

import pytest
import matplotlib.pyplot as plt

import data_analysis_agent.execution.code_executor as code_executor_module
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


def test_local_backend_stops_code_when_output_limit_is_reached(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "print('x' * 400)\n"
        "reached_after_output_limit = True",
        limits=ExecutionLimits(max_output_bytes=64),
    )

    result = executor.execute(request)

    assert result.error_code is ExecutionErrorCode.OUTPUT_LIMIT
    assert "reached_after_output_limit" not in executor.shell.user_ns
    assert len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) <= 64


def test_local_backend_classifies_overdue_in_process_execution(tmp_path):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "import time\n"
        "time.sleep(0.05)",
        limits=ExecutionLimits(timeout_seconds=0.01),
    )

    started = time.perf_counter()
    result = executor.execute(request)

    assert time.perf_counter() - started < 1.0
    assert result.success is False
    assert result.timed_out is True
    assert result.error_code is ExecutionErrorCode.TIMEOUT


def test_output_collection_stops_before_hashing_beyond_file_limit(tmp_path, monkeypatch):
    request = make_request(
        tmp_path,
        "pass",
        limits=ExecutionLimits(max_files=1, max_output_bytes=1024),
    )
    (request.output_dir / "a.bin").write_bytes(b"a")
    (request.output_dir / "b.bin").write_bytes(b"b")
    hashed: list[str] = []
    original = LocalCodeExecutor._file_metadata

    def record_hash(root, path):
        hashed.append(path.name)
        return original(root, path)

    monkeypatch.setattr(LocalCodeExecutor, "_file_metadata", staticmethod(record_hash))

    files, error_code, _ = LocalCodeExecutor._collect_output_files(request)

    assert files == ()
    assert error_code is ExecutionErrorCode.FILE_LIMIT
    assert hashed == ["a.bin"]


def test_output_collection_stops_before_hashing_beyond_byte_limit(tmp_path, monkeypatch):
    request = make_request(
        tmp_path,
        "pass",
        limits=ExecutionLimits(max_files=10, max_output_bytes=5),
    )
    (request.output_dir / "a.bin").write_bytes(b"1234")
    (request.output_dir / "b.bin").write_bytes(b"5678")
    hashed: list[str] = []
    original = LocalCodeExecutor._file_metadata

    def record_hash(root, path):
        hashed.append(path.name)
        return original(root, path)

    monkeypatch.setattr(LocalCodeExecutor, "_file_metadata", staticmethod(record_hash))

    files, error_code, _ = LocalCodeExecutor._collect_output_files(request)

    assert files == ()
    assert error_code is ExecutionErrorCode.OUTPUT_LIMIT
    assert hashed == ["a.bin"]


def test_output_collection_does_not_materialize_candidates_after_byte_limit(
    tmp_path, monkeypatch
):
    request = make_request(
        tmp_path,
        "pass",
        limits=ExecutionLimits(max_files=10, max_output_bytes=1),
    )
    root = request.output_dir.resolve()
    oversized = root / "oversized.bin"
    oversized.write_bytes(b"12")
    yielded: list[str] = []
    original_rglob = Path.rglob
    original_iterdir = Path.iterdir

    def candidates():
        yielded.append("oversized.bin")
        yield oversized
        yielded.append("beyond-limit.bin")
        yield root / "beyond-limit.bin"

    def guarded_rglob(path, pattern):
        if path != root:
            return original_rglob(path, pattern)
        return candidates()

    def guarded_iterdir(path):
        if path != root:
            return original_iterdir(path)
        return candidates()

    monkeypatch.setattr(Path, "rglob", guarded_rglob)
    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    files, error_code, _ = LocalCodeExecutor._collect_output_files(request)

    assert files == ()
    assert error_code is ExecutionErrorCode.OUTPUT_LIMIT
    assert yielded == ["oversized.bin"]


def test_output_collection_rejects_escaped_symlink_before_hashing(tmp_path, monkeypatch):
    request = make_request(tmp_path, "pass")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    escaped = request.output_dir / "escaped.bin"
    try:
        escaped.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    hashed: list[str] = []

    def fail_if_hashed(root, path):
        hashed.append(path.name)
        raise AssertionError("escaped symlink was hashed")

    monkeypatch.setattr(LocalCodeExecutor, "_file_metadata", staticmethod(fail_if_hashed))

    files, error_code, _ = LocalCodeExecutor._collect_output_files(request)

    assert files == ()
    assert error_code is ExecutionErrorCode.PATH_TRAVERSAL
    assert hashed == []


def test_local_backend_measures_duration_after_output_collection(tmp_path, monkeypatch):
    executor = LocalCodeExecutor(tmp_path / "legacy")
    request = make_request(
        tmp_path,
        "pass",
        limits=ExecutionLimits(timeout_seconds=0.1),
    )
    clock_values = iter((100.0, 100.250))
    monkeypatch.setattr(
        code_executor_module.time,
        "perf_counter",
        lambda: next(clock_values),
    )
    monkeypatch.setattr(
        executor,
        "execute_code",
        lambda code, **kwargs: {
            "success": True,
            "output": "",
            "error": "",
            "variables": {},
        },
    )
    monkeypatch.setattr(executor, "_collect_output_files", lambda request: ((), None, None))

    result = executor.execute(request)

    assert result.duration_ms == pytest.approx(250.0)
    assert result.error_code is ExecutionErrorCode.TIMEOUT


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

    before = set(plt.get_fignums())
    try:
        first.set_variable("value", 1)
        second.set_variable("value", 2)
        first.execute_code("owned_figure = plt.figure()")
        owned_by_first = set(plt.get_fignums()) - before
        assert owned_by_first

        second.reset_environment()

        assert owned_by_first <= set(plt.get_fignums())
        assert first.execute_code("print(value)")["output"].strip() == "1"
        assert second.execute_code("print('value' in globals())")["output"].strip() == "False"

        first.reset_environment()
        assert owned_by_first.isdisjoint(set(plt.get_fignums()))
    finally:
        for figure_number in owned_by_first if "owned_by_first" in locals() else ():
            plt.close(figure_number)


def test_reset_does_not_close_reused_figure_number_owned_by_another_executor(tmp_path):
    first = CodeExecutor(tmp_path / "first")
    second = CodeExecutor(tmp_path / "second")

    before = set(plt.get_fignums())
    try:
        first.execute_code("owned_figure = plt.figure()")
        owned_by_first = set(plt.get_fignums()) - before
        assert len(owned_by_first) == 1
        first_number = next(iter(owned_by_first))
        first_figure = plt.figure(first_number)
        plt.close(first_figure)

        second.execute_code(f"reused_figure = plt.figure(num={first_number})")
        second_figure = plt.figure(first_number)
        assert second_figure is not first_figure
        assert first_number in set(plt.get_fignums())

        first.reset_environment()

        assert first_number in set(plt.get_fignums())

        second.reset_environment()
        assert first_number not in set(plt.get_fignums())
    finally:
        for figure_number in set(plt.get_fignums()) - before:
            plt.close(figure_number)
