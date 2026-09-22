import data_analysis_agent as package
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.domain.enums import TaskStatus
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from tests.fixtures.recording_executor import RecordingExecutor


def test_analyze_uses_orchestrator_and_keeps_legacy_result_shape(tmp_path, monkeypatch):
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="value = 5"),
        yaml_response("analysis_complete", final_report="# analysis marker"),
        yaml_response("analysis_complete", final_report="# final report"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", RecordingExecutor)

    agent = package.DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=2,
        generate_word_report=False,
    )
    result = agent.analyze("offline")

    assert result["final_report"] == "# final report"
    assert result["total_rounds"] == 2
    assert agent.orchestrator.checkpoint().state.status is TaskStatus.COMPLETED


def test_quick_analysis_still_hides_compatibility_upload_paths(tmp_path, monkeypatch):
    source = tmp_path / "private.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="df = load_dataset(dataset_ids[0])"),
        yaml_response("analysis_complete", final_report="# marker"),
        yaml_response("analysis_complete", final_report="# final"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    result = package.quick_analysis(
        "private data",
        files=[str(source)],
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
    )

    assert result["final_report"] == "# final"
    assert str(source) not in "\n".join(call.prompt for call in fake_llm.calls)
