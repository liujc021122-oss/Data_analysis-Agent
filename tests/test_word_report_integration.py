from pathlib import Path
from types import SimpleNamespace

import data_analysis_agent as agent_module
from data_analysis_agent import DataAnalysisAgent


class FakeLLM:
    def call(self, **kwargs):
        return "unused"

    def parse_yaml_response(self, response):
        return {
            "action": "analysis_complete",
            "final_report": "# 测试报告\n\n## 结论\n\n报告正文。",
        }


def make_agent(tmp_path, generate_word_report=True):
    agent = object.__new__(DataAnalysisAgent)
    agent.conversation_history = []
    agent.analysis_results = []
    agent.current_round = 1
    agent.session_output_dir = str(tmp_path)
    agent.config = SimpleNamespace(max_tokens=128)
    agent.llm = FakeLLM()
    agent.generate_word_report = generate_word_report
    return agent


def test_final_report_generation_writes_word_file_and_returns_path(tmp_path):
    agent = make_agent(tmp_path)

    result = agent._generate_final_report()

    markdown_path = Path(result["report_file_path"])
    word_path = Path(result["word_report_file_path"])

    assert markdown_path.exists()
    assert word_path.exists()
    assert word_path.suffix == ".docx"
    assert result["word_report_generated"] is True
    assert result["word_report_error"] is None


def test_word_conversion_failure_keeps_markdown_and_reports_error(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)

    def fail_conversion(**kwargs):
        raise RuntimeError("转换器不可用")

    monkeypatch.setattr("data_analysis_agent.agent.core.generate_word_report", fail_conversion)

    result = agent._generate_final_report()

    assert Path(result["report_file_path"]).exists()
    assert result["word_report_generated"] is False
    assert "转换器不可用" in result["word_report_error"]


def test_word_generation_can_be_disabled_without_affecting_markdown(tmp_path):
    agent = make_agent(tmp_path, generate_word_report=False)

    result = agent._generate_final_report()

    assert Path(result["report_file_path"]).exists()
    assert result["word_report_file_path"] is None
    assert result["word_report_generated"] is False
    assert result["word_report_error"] is None
