from pathlib import Path
from uuid import UUID

import pytest

from data_analysis_agent import DataAnalysisAgent, quick_analysis
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.storage.models import StorageObject
from tests.fixtures.fake_llm import FakeLLM, session_dir_from_prompt, yaml_response


class RecordingStorage:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.delete_calls = []

    def put(self, stream, *, key, content_type, max_bytes=None):
        payload = stream.read()
        checksum = "sha256:" + __import__("hashlib").sha256(payload).hexdigest()
        self.put_calls.append((key, content_type))
        uri = f"fake://{key}"
        self.objects[uri] = payload
        return StorageObject(
            uri=uri,
            key=key,
            size_bytes=len(payload),
            checksum=checksum,
            content_type=content_type,
        )

    def get(self, uri):
        from io import BytesIO

        return BytesIO(self.objects[uri])

    def stat(self, uri):
        payload = self.objects[uri]
        checksum = "sha256:" + __import__("hashlib").sha256(payload).hexdigest()
        key = uri.removeprefix("fake://")
        return StorageObject(
            uri=uri,
            key=key,
            size_bytes=len(payload),
            checksum=checksum,
            content_type="application/octet-stream",
        )

    def exists(self, uri):
        return uri in self.objects

    def delete(self, uri):
        self.delete_calls.append(uri)
        self.objects.pop(uri, None)

    def create_download_url(self, uri, *, expires_in=300):
        return f"fake-download://{uri.removeprefix('fake://')}?expires={expires_in}"


class OfflineExecutor:
    def __init__(self, output_dir):
        self.variables = {}

    def set_variable(self, name, value):
        self.variables[name] = value

    def set_sensitive_columns(self, names):
        pass

    def get_environment_info(self):
        return f"session_output_dir = '{self.variables.get('session_output_dir', 'offline')}'"

    def execute_code(self, code):
        return {"success": True, "output": "", "error": "", "variables": {}}

    def reset_environment(self):
        pass


def _settings(tmp_path):
    return load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "datasets"),
            "OUTPUT_DIR": str(tmp_path / "outputs"),
        },
        dotenv_dir=tmp_path,
    )


def _flow_llm(*, word_failure=False):
    def generate_code(fake_llm, call):
        session_dir = Path(session_dir_from_prompt(call.system_prompt))
        (session_dir / "trend.png").write_bytes(b"fake chart")
        return yaml_response("generate_code", code="pass")

    def collect_figures(fake_llm, call):
        session_dir = Path(session_dir_from_prompt(call.system_prompt))
        return yaml_response(
            "collect_figures",
            figures_to_collect=[
                {
                    "figure_number": 1,
                    "filename": "trend.png",
                    "file_path": str(session_dir / "trend.png"),
                    "description": "trend",
                    "analysis": "trend",
                }
            ],
        )

    return FakeLLM(
        [
            generate_code,
            collect_figures,
            yaml_response("analysis_complete", final_report="# Stored report"),
        ]
    )


def test_storage_backed_analysis_uploads_chart_and_markdown_without_local_download_paths(
    tmp_path, monkeypatch, capsys
):
    storage = RecordingStorage()
    fake_llm = _flow_llm()
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", OfflineExecutor)

    result = quick_analysis(
        "store artifacts",
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
        settings=_settings(tmp_path),
        storage=storage,
    )

    task_id = result["task_id"]
    assert isinstance(task_id, UUID)
    keys = [key for key, _content_type in storage.put_calls]
    assert any(key.startswith(f"tasks/{task_id}/charts/") for key in keys)
    assert any(key.startswith(f"tasks/{task_id}/reports/") for key in keys)
    assert result["report_download_url"].startswith("fake-download://")
    assert str(tmp_path) not in result["report_download_url"]
    assert result["word_report_download_url"] is None
    assert Path(result["report_file_path"]).exists()
    assert len(result["artifact_records"]) == 2
    assert str(tmp_path) not in capsys.readouterr().out


def test_word_failure_keeps_markdown_storage_artifact_and_legacy_report(tmp_path, monkeypatch):
    storage = RecordingStorage()
    fake_llm = _flow_llm(word_failure=True)
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", OfflineExecutor)

    def fail_word(**kwargs):
        raise RuntimeError("planned Word failure")

    monkeypatch.setattr("data_analysis_agent.agent.core.generate_word_report", fail_word)

    result = quick_analysis(
        "word failure keeps markdown",
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=True,
        settings=_settings(tmp_path),
        storage=storage,
    )

    assert result["report_download_url"].startswith("fake-download://")
    assert result["word_report_download_url"] is None
    assert Path(result["report_file_path"]).read_text(encoding="utf-8") == "# Stored report"
    assert result["word_report_generated"] is False
    assert "planned Word failure" in result["word_report_error"]
    assert any("/reports/" in key and key.endswith("_最终分析报告.md") for key, _ in storage.put_calls)
    assert not any(key.endswith("_最终分析报告.docx") for key, _ in storage.put_calls)


def test_failed_analysis_cleans_staged_directory_and_uploaded_artifacts(tmp_path, monkeypatch):
    storage = RecordingStorage()
    fake_llm = FakeLLM([yaml_response("analysis_complete", final_report="# unused")])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    def fail_after_upload(agent):
        staged_report = Path(agent.session_output_dir) / "partial.md"
        staged_report.write_text("partial", encoding="utf-8")
        agent._store_report_artifact(
            str(staged_report),
            filename="partial.md",
            mime_type="text/markdown; charset=utf-8",
            format="MARKDOWN",
        )
        raise RuntimeError("planned analysis failure")

    monkeypatch.setattr(DataAnalysisAgent, "_generate_final_report", fail_after_upload)

    with pytest.raises(RuntimeError, match="planned analysis failure"):
        quick_analysis(
            "cleanup failed analysis",
            output_dir=tmp_path / "outputs",
            max_rounds=1,
            generate_word_report=False,
            settings=_settings(tmp_path),
            storage=storage,
        )

    assert storage.objects == {}
    assert not list((tmp_path / "outputs").glob("session_*"))


def test_reused_agent_allocates_a_new_task_id_for_each_analysis(tmp_path, monkeypatch):
    fake_llm = FakeLLM(
        [
            yaml_response("analysis_complete", final_report="# first"),
            yaml_response("analysis_complete", final_report="# first report"),
            yaml_response("analysis_complete", final_report="# second"),
            yaml_response("analysis_complete", final_report="# second report"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", OfflineExecutor)

    agent = DataAnalysisAgent(
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
    )

    first = agent.analyze("first analysis")
    second = agent.analyze("second analysis")

    assert first["task_id"] != second["task_id"]


def test_chart_path_outside_staging_is_rejected_without_suppressing_markdown(
    tmp_path, monkeypatch, capsys
):
    outside_chart = tmp_path / "outside.png"
    outside_chart.write_bytes(b"outside chart")
    storage = RecordingStorage()

    def generate_code(fake_llm, call):
        return yaml_response("generate_code", code="pass")

    def collect_figures(fake_llm, call):
        return yaml_response(
            "collect_figures",
            figures_to_collect=[
                {
                    "figure_number": 1,
                    "filename": "outside.png",
                    "file_path": str(outside_chart),
                    "description": "outside",
                    "analysis": "outside",
                }
            ],
        )

    fake_llm = FakeLLM(
        [
            generate_code,
            collect_figures,
            yaml_response("analysis_complete", final_report="# Safe report"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", OfflineExecutor)

    result = quick_analysis(
        "reject outside chart",
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
        settings=_settings(tmp_path),
        storage=storage,
    )

    assert result["report_download_url"].startswith("fake-download://")
    assert not any("/charts/" in key for key, _ in storage.put_calls)
    assert result["storage_error"] is not None
    assert str(outside_chart) not in result["storage_error"]
    assert str(outside_chart) not in capsys.readouterr().out
