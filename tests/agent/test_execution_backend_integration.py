from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from data_analysis_agent import DataAnalysisAgent, LLMConfig
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.execution import ExecutionResult
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
