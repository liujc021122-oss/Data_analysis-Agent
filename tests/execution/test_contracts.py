from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.execution import (
    CodeExecutionBackend,
    ExecutionFile,
    ExecutionInput,
    ExecutionLimits,
    ExecutionRequest,
    ExecutionResult,
    MAX_MEMORY_LIMIT_BYTES,
    MAX_SAFE_TEXT_LENGTH,
    MAX_TIMEOUT_SECONDS,
    NetworkPolicy,
)


def test_execution_request_defaults_to_disabled_network_and_serializes():
    request = ExecutionRequest(
        task_id=uuid4(),
        code="print('ok')",
        output_dir=Path("outputs") / "task-1",
    )

    assert request.network_policy is NetworkPolicy.DISABLED
    assert request.output_dir.is_absolute()
    assert request.code_sha256 == sha256(b"print('ok')").hexdigest()
    assert request.model_dump_json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", MAX_TIMEOUT_SECONDS + 1),
        ("memory_limit_bytes", 0),
        ("memory_limit_bytes", MAX_MEMORY_LIMIT_BYTES + 1),
        ("cpu_limit", 0),
        ("pids_limit", 0),
        ("max_output_bytes", 0),
        ("max_files", 0),
    ],
)
def test_execution_limits_reject_non_positive_or_over_bound_values(field, value):
    with pytest.raises(ValidationError, match=field):
        ExecutionLimits(**{field: value})


def test_execution_input_is_read_only_and_must_stay_in_source_scope(tmp_path):
    source_root = tmp_path / "input"
    source_root.mkdir()
    source_path = source_root / "sample.csv"

    item = ExecutionInput(
        logical_name="sample.csv",
        source_path=source_path,
        source_scope=source_root,
    )

    assert item.read_only is True
    assert item.resolved_source_path == source_path.resolve()

    with pytest.raises(ValidationError, match="PATH_TRAVERSAL"):
        ExecutionInput(
            logical_name="sample.csv",
            source_path=tmp_path / "outside.csv",
            source_scope=source_root,
        )


@pytest.mark.parametrize("logical_name", ["", ".", "..", "../secret", r"..\secret", "/etc/passwd", r"C:\secret"])
def test_execution_input_rejects_unsafe_logical_names(tmp_path, logical_name):
    source_root = tmp_path / "input"
    source_root.mkdir()

    with pytest.raises(ValidationError, match="PATH_TRAVERSAL"):
        ExecutionInput(
            logical_name=logical_name,
            source_path=source_root / "sample.csv",
            source_scope=source_root,
        )


def test_execution_request_rejects_output_directory_outside_scope(tmp_path):
    output_root = tmp_path / "outputs"
    output_root.mkdir()

    with pytest.raises(ValidationError, match="PATH_TRAVERSAL"):
        ExecutionRequest(
            task_id=uuid4(),
            code="pass",
            output_dir=tmp_path / "outside",
            output_scope=output_root,
        )


def test_execution_file_derives_suffix_and_validates_sha256():
    digest = sha256(b"png").hexdigest()
    artifact = ExecutionFile(
        logical_name="charts/chart.png",
        size_bytes=3,
        sha256=digest,
        mime_type="image/png",
    )

    assert artifact.suffix == ".png"
    assert artifact.model_dump_json()

    with pytest.raises(ValidationError, match="sha256"):
        ExecutionFile(logical_name="chart.png", size_bytes=3, sha256="bad")


def test_execution_result_is_bounded_and_redacts_secrets_and_host_paths():
    secret = "top-secret-value"
    host_path = r"C:\Users\analyst\workspace\private.csv"
    result = ExecutionResult(
        success=False,
        stdout=("x" * (MAX_SAFE_TEXT_LENGTH + 100)) + " OPENAI_API_KEY=" + secret,
        stderr=f"failed at {host_path}",
        error_message=f"OPENAI_API_KEY={secret}; source={host_path}",
        code_sha256=sha256(b"pass").hexdigest(),
        duration_ms=1.0,
    )

    assert len(result.stdout) <= MAX_SAFE_TEXT_LENGTH
    assert secret not in result.stdout
    assert secret not in result.error_message
    assert host_path not in result.stderr
    assert "[REDACTED]" in result.error_message
    assert result.model_dump_json()


def test_execution_backend_protocol_describes_synchronous_execution():
    class FakeBackend:
        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            return ExecutionResult(
                success=True,
                code_sha256=request.code_sha256,
                duration_ms=0,
            )

    backend = FakeBackend()
    assert isinstance(backend, CodeExecutionBackend)
