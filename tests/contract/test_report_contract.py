from pathlib import Path
from types import SimpleNamespace

from docx import Document

from data_analysis_agent import DataAnalysisAgent
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from data_analysis_agent.reports.word import generate_word_report


SAMPLE_CHART = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chart.png"


def make_report_agent(tmp_path, markdown, generate_word=True):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    agent = object.__new__(DataAnalysisAgent)
    agent.session_output_dir = str(session_dir)
    agent.analysis_results = []
    agent.current_round = 1
    agent.conversation_history = []
    agent.config = SimpleNamespace(max_tokens=128)
    agent.generate_word_report = generate_word
    agent.llm = FakeLLM(
        [yaml_response("analysis_complete", final_report=markdown)]
    )
    return agent, session_dir


def test_final_report_contract_writes_markdown_and_returns_paths(tmp_path):
    markdown = "# 基线报告\n\nMarkdown 正文。"
    agent, session_dir = make_report_agent(tmp_path, markdown, generate_word=False)

    result = agent._generate_final_report()

    report_path = Path(result["report_file_path"])
    assert report_path == session_dir / "最终分析报告.md"
    assert report_path.read_text(encoding="utf-8") == markdown
    assert result["final_report"] == markdown
    assert result["word_report_file_path"] is None
    assert result["word_report_generated"] is False
    assert result["word_report_error"] is None


def test_word_generation_failure_keeps_markdown_and_exposes_report_error(tmp_path, monkeypatch):
    markdown = "# Word 失败兜底\n\nMarkdown 必须保留。"
    agent, _ = make_report_agent(tmp_path, markdown, generate_word=True)

    def fail_word_generation(**kwargs):
        raise RuntimeError("baseline Word failure")

    monkeypatch.setattr("data_analysis_agent.agent.core.generate_word_report", fail_word_generation)

    result = agent._generate_final_report()

    report_path = Path(result["report_file_path"])
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8") == markdown
    assert result["word_report_generated"] is False
    assert result["word_report_file_path"] is not None
    assert "baseline Word failure" in result["word_report_error"]


def test_image_outside_session_directory_is_not_embedded(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    output_path = session_dir / "最终分析报告.docx"
    markdown = "# 图片边界\n\n![越界图片](./sample_chart.png)"

    generate_word_report(
        markdown_content=markdown,
        output_path=output_path,
        session_output_dir=session_dir,
        figures=[
            {
                "filename": "sample_chart.png",
                "file_path": str(SAMPLE_CHART),
            }
        ],
    )

    document = Document(output_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "图片不可用" in paragraph_text
    assert len(document.inline_shapes) == 0
