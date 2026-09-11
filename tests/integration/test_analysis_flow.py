from pathlib import Path

from docx import Document

import data_analysis_agent as agent_module
from tests.fixtures.fake_llm import FakeLLM, session_dir_from_prompt, yaml_response


SAMPLE_DATA = Path(__file__).resolve().parents[1] / "fixtures" / "sample_data.csv"


def make_normal_flow_llm():
    code = "\n".join(
        [
            f"df = pd.read_csv({str(SAMPLE_DATA)!r})",
            "figure_path = os.path.abspath(os.path.join(session_output_dir, 'sales_trend.png'))",
            "plt.figure(figsize=(4, 3))",
            "plt.plot(df['date'], df['value'])",
            "plt.title('Value trend')",
            "plt.savefig(figure_path)",
            "plt.close()",
            "print(figure_path)",
        ]
    )

    def collect_figures_response(fake_llm, call):
        session_dir = Path(session_dir_from_prompt(call.system_prompt))
        chart_path = session_dir / "sales_trend.png"
        return yaml_response(
            "collect_figures",
            figures_to_collect=[
                {
                    "figure_number": 1,
                    "filename": "sales_trend.png",
                    "file_path": str(chart_path),
                    "description": "样例数据的数值趋势",
                    "analysis": "数值随日期变化的趋势。",
                }
            ],
        )

    markdown = (
        "# 离线分析报告\n\n"
        "## 趋势结论\n\n"
        "【部分总结】样例数据已完成趋势分析。\n"
        "【分析要点】\n"
        "- 图表由本地执行器生成。\n\n"
        "![数值趋势](./sales_trend.png)\n"
    )

    return FakeLLM(
        [
            yaml_response("generate_code", code=code),
            collect_figures_response,
            yaml_response("analysis_complete", final_report="# 分析循环完成"),
            yaml_response("analysis_complete", final_report=markdown),
        ]
    )


def test_quick_analysis_runs_complete_offline_flow_and_generates_chart_and_reports(
    tmp_path, monkeypatch
):
    fake_llm = make_normal_flow_llm()
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    result = agent_module.quick_analysis(
        query="分析样例数据并生成趋势图",
        files=[str(SAMPLE_DATA)],
        output_dir=str(tmp_path / "outputs"),
        max_rounds=5,
    )

    session_dir = Path(result["session_output_dir"])
    chart_path = session_dir / "sales_trend.png"
    markdown_path = Path(result["report_file_path"])
    word_path = Path(result["word_report_file_path"])

    assert result["total_rounds"] == 3
    assert len(fake_llm.calls) == 4
    assert str(SAMPLE_DATA) in fake_llm.calls[0].prompt
    assert chart_path.exists()
    assert markdown_path.exists()
    assert "离线分析报告" in markdown_path.read_text(encoding="utf-8")
    assert result["collected_figures"][0]["file_path"] == str(chart_path)
    assert result["word_report_generated"] is True
    assert word_path.exists()
    assert len(Document(word_path).inline_shapes) == 1


def test_analysis_feeds_executor_failure_back_to_llm_and_continues(tmp_path, monkeypatch):
    recovery_code = (
        f"df = pd.read_csv({str(SAMPLE_DATA)!r})\n"
        "print(df['value'].mean())"
    )
    fake_llm = FakeLLM(
        [
            yaml_response(
                "generate_code",
                code="raise ValueError('planned executor failure')",
            ),
            yaml_response("generate_code", code=recovery_code),
            yaml_response("analysis_complete", final_report="# 循环完成"),
            yaml_response("analysis_complete", final_report="# 恢复后的报告"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    result = agent_module.quick_analysis(
        query="验证执行失败后的自动反馈",
        files=[str(SAMPLE_DATA)],
        output_dir=str(tmp_path / "outputs"),
        max_rounds=5,
        generate_word_report=False,
    )

    assert result["total_rounds"] == 3
    assert len(result["analysis_results"]) == 2
    assert result["analysis_results"][0]["result"]["success"] is False
    assert result["analysis_results"][1]["result"]["success"] is True
    assert "代码执行失败" in fake_llm.calls[1].prompt
    assert "planned executor failure" in fake_llm.calls[1].prompt
    assert result["final_report"] == "# 恢复后的报告"
