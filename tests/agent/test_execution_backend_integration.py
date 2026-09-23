from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from data_analysis_agent import DataAnalysisAgent, LLMConfig
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.execution import (
    CleanupFailureError,
    ContainerCodeExecutor,
    ExecutionErrorCode,
    ExecutionResult,
)
from data_analysis_agent.execution.agent_session import AgentExecutionSession
from tests.fixtures.fake_llm import FakeLLM, yaml_response


class RecordingBackend:
    production_safe = True

    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return ExecutionResult(
            success=True,
            stdout="container output",
            stderr="",
            exit_code=0,
            code_sha256=request.code_sha256,
            duration_ms=1,
        )


class UnsafeBackend(RecordingBackend):
    production_safe = False


class FailingBackend(RecordingBackend):
    def execute(self, request):
        self.requests.append(request)
        return ExecutionResult(
            success=False,
            stdout="partial output",
            stderr="",
            exit_code=137,
            timed_out=True,
            error_code=ExecutionErrorCode.TIMEOUT,
            error_message="container execution timed out",
            code_sha256=request.code_sha256,
            duration_ms=3,
        )


def production_settings(tmp_path: Path):
    return load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "DATABASE_URL": "mysql+pymysql://user:password@db.invalid/data",
            "STORAGE_ENDPOINT": "https://storage.invalid",
            "STORAGE_BUCKET": "analysis",
            "EXECUTION_IMAGE": "analysis:offline",
        },
        dotenv_dir=tmp_path,
    )


def test_agent_execution_session_builds_typed_request_with_fixed_container_paths(
    tmp_path,
):
    backend = RecordingBackend()
    host_output = tmp_path / "private-output"
    session = AgentExecutionSession(
        backend=backend,
        output_dir=host_output,
        output_scope=tmp_path,
        task_id=uuid4(),
    )
    try:
        session.set_variable("session_output_dir", host_output)
        result = session.execute_code("print(session_output_dir)")
    finally:
        session.close()

    request = backend.requests[0]
    assert result["success"] is True
    assert request.output_dir == host_output.resolve()
    assert str(host_output) not in request.code
    assert "session_output_dir = '/output'" in request.code
    assert "print(session_output_dir)" in request.code


def test_agent_execution_session_stages_dataset_ids_as_read_only_inputs(tmp_path):
    backend = RecordingBackend()
    dataset_id = str(uuid4())
    session = AgentExecutionSession(
        backend=backend,
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    session.set_variable("dataset_ids", (dataset_id,))
    session.set_variable(
        "load_dataset", lambda requested: pd.DataFrame({"value": [1, 2]})
    )
    try:
        session.execute_code("df = load_dataset(dataset_ids[0])")
    finally:
        session.close()

    request = backend.requests[0]
    assert request.input_files[0].logical_name == f"datasets/{dataset_id}.csv"
    assert request.input_files[0].read_only is True
    assert f"/input/datasets/{dataset_id}.csv" in request.code
    assert str(tmp_path) not in request.code


def test_agent_discards_container_figure_paths_outside_output_scope(tmp_path):
    session = AgentExecutionSession(
        backend=RecordingBackend(),
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    agent = object.__new__(DataAnalysisAgent)
    agent.executor = session

    try:
        assert agent._resolve_executor_path("/output/../private.png") == ""
    finally:
        session.close()


def test_agent_drops_invalid_container_figure_from_collection(tmp_path):
    session = AgentExecutionSession(
        backend=RecordingBackend(),
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    agent = object.__new__(DataAnalysisAgent)
    agent.executor = session
    agent._display_text = str

    try:
        result = agent._handle_collect_figures(
            "",
            {
                "figures_to_collect": [
                    {
                        "figure_number": 1,
                        "filename": "private.png",
                        "file_path": "/output/../private.png",
                    }
                ]
            },
        )
    finally:
        session.close()

    assert result["collected_figures"] == []


def test_agent_execution_session_serializes_python_boolean_and_none_variables(tmp_path):
    backend = RecordingBackend()
    session = AgentExecutionSession(
        backend=backend,
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    session.set_variable("flag", True)
    session.set_variable("missing", None)
    try:
        session.execute_code("assert flag is True\nassert missing is None")
    finally:
        session.close()

    assert "flag = True" in backend.requests[0].code
    assert "missing = None" in backend.requests[0].code


def test_agent_execution_session_retries_input_cleanup_after_failure(tmp_path, monkeypatch):
    session = AgentExecutionSession(
        backend=RecordingBackend(),
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    original_rmtree = __import__(
        "data_analysis_agent.execution.agent_session", fromlist=["shutil"]
    ).shutil.rmtree
    calls = 0

    def fail_once(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("private cleanup detail")
        return original_rmtree(path)

    monkeypatch.setattr(
        "data_analysis_agent.execution.agent_session.shutil.rmtree", fail_once
    )

    with pytest.raises(CleanupFailureError, match="cleanup"):
        session.close()
    assert session._closed is False

    session.close()
    assert calls == 2


def test_agent_execution_session_exposes_minimal_audit_metadata(tmp_path):
    backend = RecordingBackend()
    host_output = tmp_path / "private-output"
    session = AgentExecutionSession(
        backend=backend,
        output_dir=host_output,
        output_scope=tmp_path,
        task_id=uuid4(),
    )
    try:
        result = session.execute_code("print('ordinary')")
        audit = result["audit"]
    finally:
        session.close()

    assert audit["task_id"] == str(session.task_id)
    assert audit["backend"] == "RecordingBackend"
    assert audit["code_sha256"] == backend.requests[0].code_sha256
    assert audit["success"] is True
    assert audit["exit_code"] == 0
    assert audit["duration_ms"] == 1.0
    assert "code" not in audit
    assert str(host_output) not in repr(audit)


def test_agent_execution_session_audits_typed_failure_without_source_code(tmp_path):
    backend = FailingBackend()
    source_code = "raise RuntimeError('private source')"
    session = AgentExecutionSession(
        backend=backend,
        output_dir=tmp_path / "outputs" / "task",
        output_scope=tmp_path / "outputs",
        task_id=uuid4(),
    )
    try:
        result = session.execute_code(source_code)
    finally:
        session.close()

    assert result["success"] is False
    assert result["error_code"] == ExecutionErrorCode.TIMEOUT.value
    assert result["audit"]["error_code"] == ExecutionErrorCode.TIMEOUT.value
    assert result["audit"]["timed_out"] is True
    assert source_code not in repr(result["audit"])


def test_production_agent_uses_injected_backend_without_constructing_legacy_executor(
    tmp_path, monkeypatch
):
    backend = RecordingBackend()
    fake_llm = FakeLLM(
        [
            yaml_response("generate_code", code="print('offline')"),
            yaml_response("analysis_complete", final_report="# final"),
        ]
    )

    def forbidden_legacy_executor(*args, **kwargs):
        raise AssertionError("production path constructed CodeExecutor")

    monkeypatch.setattr(
        "data_analysis_agent.agent.core.CodeExecutor", forbidden_legacy_executor
    )
    settings = production_settings(tmp_path)
    agent = DataAnalysisAgent(
        llm_config=LLMConfig(
            api_key="offline-key",
            base_url="https://offline.invalid",
            model="offline-model",
        ),
        settings=settings,
        execution_backend=backend,
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        llm=fake_llm,
    )

    result = agent.analyze("offline")

    assert result["final_report"] == "# final"
    assert len(backend.requests) == 1
    assert result["analysis_results"][0]["result"]["success"] is True
    assert result["execution_audits"][0]["backend"] == "RecordingBackend"
    assert result["execution_audits"][0]["code_sha256"] == backend.requests[0].code_sha256


def test_production_agent_builds_container_backend_when_not_injected(tmp_path, monkeypatch):
    backend = RecordingBackend()
    calls = {}

    def build_backend(settings, *, runtime):
        calls["settings"] = settings
        calls["runtime"] = runtime
        return backend

    monkeypatch.setattr(
        "data_analysis_agent.agent.core.build_execution_backend", build_backend
    )
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.CodeExecutor",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("production path constructed CodeExecutor")
        ),
    )
    settings = production_settings(tmp_path)
    runtime = object()
    agent = DataAnalysisAgent(
        settings=settings,
        execution_runtime=runtime,
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        llm=FakeLLM(
            [
                yaml_response("generate_code", code="print('offline')"),
                yaml_response("analysis_complete", final_report="# final"),
            ]
        ),
    )

    result = agent.analyze("offline")

    assert result["final_report"] == "# final"
    assert calls["settings"] is settings
    assert calls["runtime"] is runtime
    assert isinstance(agent.executor, AgentExecutionSession)
    assert not isinstance(agent.executor.backend, ContainerCodeExecutor)


def test_agent_defaults_to_environment_settings_for_production_fail_closed(
    tmp_path, monkeypatch
):
    settings = production_settings(tmp_path)
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: settings)
    agent = DataAnalysisAgent(
        execution_backend=UnsafeBackend(),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        llm=FakeLLM([]),
    )

    with pytest.raises(ConfigurationError, match="production-safe"):
        agent.analyze("offline")


def test_agent_uses_its_production_settings_for_compatibility_files(
    tmp_path, monkeypatch
):
    production = production_settings(tmp_path)
    development = load_settings(app_env="development", environ={}, dotenv_dir=tmp_path)
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: development)

    @contextmanager
    def fake_upload(files, *, settings):
        del files
        if settings.app_env == "production":
            raise ConfigurationError("production files uploads require the production upload service")
        yield (), object(), uuid4()

    monkeypatch.setattr("data_analysis_agent.agent.core._upload_compatibility_files", fake_upload)
    agent = DataAnalysisAgent(
        settings=production,
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        llm=FakeLLM([yaml_response("analysis_complete", final_report="# final")]),
    )

    with pytest.raises(ConfigurationError, match="production files"):
        agent.analyze("offline", files=[str(tmp_path / "sample.csv")])


def test_production_agent_rejects_unsafe_injected_backend(tmp_path):
    settings = production_settings(tmp_path)
    agent = DataAnalysisAgent(
        llm_config=LLMConfig(
            api_key="offline-key",
            base_url="https://offline.invalid",
            model="offline-model",
        ),
        settings=settings,
        execution_backend=UnsafeBackend(),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        llm=FakeLLM(
            [
                yaml_response("generate_code", code="print('offline')"),
                yaml_response("analysis_complete", final_report="# final"),
            ]
        ),
    )

    with pytest.raises(ConfigurationError, match="production-safe"):
        agent.analyze("offline")
