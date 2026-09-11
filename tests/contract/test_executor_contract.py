from pathlib import Path

from data_analysis_agent.execution.code_executor import CodeExecutor


SAMPLE_DATA = Path(__file__).resolve().parents[1] / "fixtures" / "sample_data.csv"


def test_executor_runs_analysis_code_against_sample_data(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))
    code = (
        f"df = pd.read_csv({str(SAMPLE_DATA)!r})\n"
        "print(df['value'].sum())"
    )

    result = executor.execute_code(code)

    assert result["success"] is True
    assert "41" in result["output"]
    assert result["error"] == ""


def test_executor_returns_structured_error_for_failed_code(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))

    result = executor.execute_code("raise RuntimeError('executor contract failure')")

    assert result["success"] is False
    assert "执行错误" in result["error"]
    assert "executor contract failure" in result["error"]
    assert result["variables"] == {}


def test_executor_saves_chart_inside_session_directory(tmp_path):
    session_dir = tmp_path / "session"
    executor = CodeExecutor(str(session_dir))
    executor.set_variable("session_output_dir", str(session_dir))
    code = """
plt.figure(figsize=(4, 3))
plt.plot([1, 2, 3], [2, 4, 3])
figure_path = os.path.abspath(os.path.join(session_output_dir, 'sample_generated_chart.png'))
plt.savefig(figure_path)
plt.close()
print(figure_path)
"""

    result = executor.execute_code(code)
    chart_path = session_dir / "sample_generated_chart.png"

    assert result["success"] is True
    assert chart_path.exists()
    assert str(chart_path) in result["output"]
