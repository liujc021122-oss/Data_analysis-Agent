from pathlib import Path

import pandas as pd
import pytest
import matplotlib.pyplot as plt

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


def test_executors_have_isolated_user_namespaces(tmp_path):
    first = CodeExecutor(str(tmp_path / "first"))
    second = CodeExecutor(str(tmp_path / "second"))
    first.set_variable("secret", "executor-one-only")

    result = second.execute_code("print('secret' in globals())")

    assert result["success"] is True
    assert result["output"].strip() == "False"


def test_resetting_one_executor_does_not_clear_another(tmp_path):
    first = CodeExecutor(str(tmp_path / "first"))
    second = CodeExecutor(str(tmp_path / "second"))
    first.set_variable("retained", 42)

    second.reset_environment()

    assert first.execute_code("print(retained)")["output"].strip() == "42"


def test_reset_only_closes_figures_created_by_that_executor(tmp_path):
    first = CodeExecutor(str(tmp_path / "first"))
    second = CodeExecutor(str(tmp_path / "second"))

    first.execute_code("first_figure = plt.figure()\nplt.plot([1, 2], [2, 1])")
    first_figure = first.shell.user_ns["first_figure"]
    second.execute_code("second_figure = plt.figure()\nplt.plot([1, 2], [1, 2])")
    second_figure = second.shell.user_ns["second_figure"]

    first.reset_environment()

    assert not plt.fignum_exists(first_figure.number)
    assert plt.fignum_exists(second_figure.number)


def _assert_sensitive_values_are_absent(output, sensitive_values):
    if any(value in output for value in sensitive_values):
        raise AssertionError("sensitive dataframe value leaked from executor output")


def _sensitive_dataframe():
    return pd.DataFrame(
        {
            "name": ["Alice"],
            "email": ["alice.private@example.test"],
            "phone": ["13900001234"],
        }
    )


def test_executor_redacts_sensitive_dataframe_columns_from_print(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))
    executor.set_sensitive_columns({"email", "phone"})
    executor.set_variable("df", _sensitive_dataframe())

    result = executor.execute_code("print(df)")

    assert result["success"] is True
    _assert_sensitive_values_are_absent(
        result["output"], ("alice.private@example.test", "13900001234")
    )
    assert "Alice" in result["output"]
    assert "[REDACTED]" in result["output"]


def test_executor_redacts_sensitive_series_from_print(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))
    executor.set_sensitive_columns({"email", "phone"})
    executor.set_variable("df", _sensitive_dataframe())

    result = executor.execute_code("print(df['email'])")

    assert result["success"] is True
    _assert_sensitive_values_are_absent(
        result["output"], ("alice.private@example.test", "13900001234")
    )
    assert "[REDACTED]" in result["output"]


def test_executor_redacts_sensitive_dataframe_columns_from_final_expression(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))
    executor.set_sensitive_columns({"email", "phone"})
    executor.set_variable("df", _sensitive_dataframe())

    result = executor.execute_code("df")

    assert result["success"] is True
    _assert_sensitive_values_are_absent(
        result["output"], ("alice.private@example.test", "13900001234")
    )
    assert "Alice" in result["output"]
    assert "[REDACTED]" in result["output"]


@pytest.mark.parametrize("code", ["print(df)", "print(df['private'])", "df", "df['private']"])
def test_redaction_uses_configured_columns_without_mutating_analysis_data(tmp_path, code):
    executor = CodeExecutor(str(tmp_path / "session"))
    executor.set_sensitive_columns({"private"})
    frame = pd.DataFrame({"private": [13900001234], "ordinary": ["kept"]})
    executor.set_variable("df", frame)

    result = executor.execute_code(code)

    assert result["success"] is True
    assert "13900001234" not in result["output"]
    assert "[REDACTED]" in result["output"]
    assert frame.iloc[0]["private"] == 13900001234
    assert frame.iloc[0]["ordinary"] == "kept"


def test_sensitive_configuration_does_not_affect_other_executors(tmp_path):
    first = CodeExecutor(str(tmp_path / "first"))
    second = CodeExecutor(str(tmp_path / "second"))
    first.set_sensitive_columns({"name"})
    first.set_variable("df", pd.DataFrame({"name": ["Alice"]}))
    second.set_variable("df", pd.DataFrame({"name": ["Bob"]}))

    assert "Alice" not in first.execute_code("print(df)\ndf")["output"]
    assert "Bob" in second.execute_code("print(df)\ndf")["output"]
