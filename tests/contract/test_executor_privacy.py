"""Privacy regressions: never include fixture values in failure diagnostics."""

import pandas as pd
import pytest
from IPython.core.interactiveshell import InteractiveShell

from data_analysis_agent.execution.code_executor import CodeExecutor


def assert_private(text, values):
    if any(str(value) in text for value in values):
        raise AssertionError("sensitive value escaped the executor boundary")


@pytest.fixture
def private_executor(tmp_path):
    executor = CodeExecutor(str(tmp_path / "session"))
    executor.set_sensitive_columns({"email", "phone"})
    frame = pd.DataFrame({
        "name": ["Alice"],
        "email": ["private.contact@example.test"],
        "phone": [13900001234],
        "amount": [42],
    })
    executor.set_variable("df", frame)
    return executor, frame, (frame.at[0, "email"], frame.at[0, "phone"])


@pytest.mark.parametrize("existing_shell", [False, True])
def test_display_uses_own_executor_without_global_publisher(
    private_executor, monkeypatch, existing_shell
):
    executor, frame, values = private_executor
    external_shell = InteractiveShell() if existing_shell else None
    monkeypatch.setattr(InteractiveShell, "_instance", external_shell)
    published = []
    if external_shell is not None:
        monkeypatch.setattr(external_shell.display_pub, "publish", lambda **kw: published.append(kw))

    for reset in (False, True):
        if reset:
            executor.reset_environment()
            executor.set_variable("df", frame)
        result = executor.execute_code("display(df)\ndisplay('ordinary display')")
        assert_private(repr(result), values)
        assert result["success"] is True
        assert "Alice" in result["output"]
        assert "ordinary display" in result["output"]
        assert "[REDACTED]" in result["output"]
    if published:
        raise AssertionError("executor display reached another shell publisher")


@pytest.mark.parametrize("expression, ordinary", [
    ("df.iloc[0]", "Alice"),
    ("df.set_index('email')", "Alice"),
    ("df['email'].value_counts()", "1"),
    ("df.set_index('email').iloc[0]", "Alice"),
    ("df.set_index(['email', 'name'])", "Alice"),
])
def test_pandas_derived_representations_keep_only_ordinary_values(
    private_executor, expression, ordinary
):
    executor, frame, values = private_executor
    # Keep only the derived table: index/name handling cannot rely on df remaining.
    table = eval(expression, {"df": frame})
    executor.shell.user_ns.pop("df")
    executor.set_variable("table", table)
    before = table.copy(deep=True)

    result = executor.execute_code("print(table)\ndisplay(table)\ntable")

    assert_private(repr(result), values)
    assert result["success"] is True
    assert ordinary in result["output"]
    assert "[REDACTED]" in result["output"]
    if isinstance(table, pd.Series):
        unchanged = table.equals(before) and table.name == before.name
    else:
        unchanged = table.equals(before)
    if not unchanged:
        raise AssertionError("display modified the analysis table")


@pytest.mark.parametrize("code", [
    "print(f'{df}')",
    "df.to_string()",
    "[df]",
    "{'tables': [df, df.iloc[0]]}",
    "print([df])",
    "display(df.to_string())",
    "import sys\nsys.stderr.write(df.to_string())",
    "print(df['amount'].sum())\nraise ValueError(df.at[0, 'email'])",
])
def test_all_execution_text_exits_are_redacted(private_executor, code, capsys):
    executor, frame, values = private_executor
    result = executor.execute_code(code)
    console = capsys.readouterr()

    assert_private(repr(result) + console.out + console.err, values)
    assert result["success"] is ("raise ValueError" not in code)
    if "sys.stderr" not in code:
        assert "[REDACTED]" in result["output"] + result["error"]
    if frame.at[0, "email"] != values[0] or frame.at[0, "phone"] != values[1]:
        raise AssertionError("redaction modified source values")


@pytest.mark.parametrize("failure", ["safety", "before_exec", "outer"])
def test_failure_exits_also_redact_known_values(private_executor, monkeypatch, failure):
    executor, _, values = private_executor
    message = str(values[0])
    if failure == "safety":
        monkeypatch.setattr(executor, "_check_code_safety", lambda code: (False, message))
    elif failure == "before_exec":
        from types import SimpleNamespace

        monkeypatch.setattr(
            executor.shell,
            "run_cell",
            lambda code: SimpleNamespace(
                error_before_exec=SyntaxError(message), error_in_exec=None, result=None
            ),
        )
    else:
        def fail_run(code):
            raise RuntimeError(message)
        monkeypatch.setattr(executor.shell, "run_cell", fail_run)

    result = executor.execute_code("print('ordinary')")

    assert_private(repr(result), values)
    assert result["success"] is False
    assert "[REDACTED]" in result["error"]


def test_environment_summary_redacts_extracted_scalar(private_executor):
    executor, _, values = private_executor
    result = executor.execute_code("contact = df.at[0, 'email']\nordinary = 'Alice'")

    assert result["success"] is True
    summary = executor.get_environment_info()
    assert_private(summary, values)
    assert "Alice" in summary


def test_text_redaction_is_isolated_and_reset_clears_old_values(private_executor, tmp_path):
    first, _, values = private_executor
    first.execute_code("print(f'{df}')")
    second = CodeExecutor(str(tmp_path / "second"))
    second.set_variable("ordinary", values[0])
    if str(values[0]) not in second.execute_code("print(ordinary)")["output"]:
        raise AssertionError("one executor changed another executor's ordinary output")
    first.reset_environment()
    first.set_variable("ordinary", values[0])
    if str(values[0]) not in first.execute_code("print(ordinary)")["output"]:
        raise AssertionError("reset retained previous dataset values")
