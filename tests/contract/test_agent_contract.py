from pathlib import Path
from types import SimpleNamespace
import re

from data_analysis_agent import DataAnalysisAgent, LLMConfig
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from data_analysis_agent.services.session import create_session_output_dir
from data_analysis_agent.services.llm import LLMHelper


class RecordingExecutor:
    """Small executor double for isolating Agent state-machine behavior."""

    def __init__(self, output_dir=None, result=None):
        self.output_dir = output_dir
        self.result = result or {
            "success": True,
            "output": "5",
            "error": "",
            "variables": {},
        }
        self.calls = []
        self.variables = {}

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_environment_info(self):
        session_dir = self.variables.get("session_output_dir", self.output_dir or "offline")
        return f"图片保存目录: session_output_dir = '{session_dir}'"

    def execute_code(self, code):
        self.calls.append(code)
        return dict(self.result)

    def reset_environment(self):
        self.calls.clear()


def make_process_agent(executor, llm=None):
    agent = object.__new__(DataAnalysisAgent)
    agent.llm = llm or FakeLLM([])
    agent.executor = executor
    agent.analysis_results = []
    agent.conversation_history = []
    agent.current_round = 0
    agent.max_rounds = 20
    agent.session_output_dir = None
    agent.generate_word_report = False
    agent.config = SimpleNamespace(max_tokens=128)
    return agent


def test_generate_code_action_executes_code_and_returns_success_feedback():
    executor = RecordingExecutor()
    agent = make_process_agent(executor)

    result = agent._process_response(
        yaml_response("generate_code", code="value = 2 + 3")
    )

    assert result["action"] == "generate_code"
    assert executor.calls == ["value = 2 + 3"]
    assert result["result"]["success"] is True
    assert "代码执行成功" in result["feedback"]


def test_execution_failure_is_preserved_and_formatted_for_next_feedback():
    executor = RecordingExecutor(
        result={
            "success": False,
            "output": "partial output",
            "error": "执行错误: planned executor failure",
            "variables": {},
        }
    )
    agent = make_process_agent(executor)

    result = agent._process_response(
        yaml_response("generate_code", code="raise ValueError('planned executor failure')")
    )

    assert result["action"] == "generate_code"
    assert result["result"]["success"] is False
    assert "代码执行失败" in result["feedback"]
    assert "planned executor failure" in result["feedback"]


def test_empty_llm_response_becomes_invalid_response_without_executor_call():
    executor = RecordingExecutor()
    agent = make_process_agent(executor)

    result = agent._process_response("")

    assert result["action"] == "invalid_response"
    assert result["error"] == "响应中缺少可执行代码"
    assert result["continue"] is True
    assert executor.calls == []


def test_invalid_yaml_becomes_invalid_response_and_identifies_model_boundary():
    executor = RecordingExecutor()
    agent = make_process_agent(executor)

    result = agent._process_response("action: [unterminated")

    assert result["action"] == "invalid_response"
    assert result["error"] == "响应中缺少可执行代码"
    assert executor.calls == []


def test_unknown_action_falls_back_to_generate_code_contract():
    executor = RecordingExecutor()
    agent = make_process_agent(executor)

    result = agent._process_response(
        yaml_response("future_action", code="value = 42")
    )

    assert result["action"] == "generate_code"
    assert result["code"] == "value = 42"
    assert executor.calls == ["value = 42"]


def test_llm_yaml_parser_returns_none_for_empty_and_mapping_for_invalid_yaml():
    helper = object.__new__(LLMHelper)

    assert helper.parse_yaml_response("") is None
    assert helper.parse_yaml_response("action: [unterminated") == {}


def test_model_call_failure_is_normalized_to_empty_response(capsys):
    class FailingClient:
        async def chat_completions_create(self, **kwargs):
            raise RuntimeError("fake model outage")

    helper = object.__new__(LLMHelper)
    helper.config = LLMConfig(
        api_key="offline-test-key",
        base_url="https://offline.invalid",
        model="fake-model",
    )
    helper.client = FailingClient()

    assert helper.call("offline prompt") == ""
    assert "LLM调用失败" in capsys.readouterr().out


def test_analysis_stops_at_max_rounds_and_still_generates_final_report(tmp_path, monkeypatch):
    fake_llm = FakeLLM(
        [
            yaml_response("generate_code", code="first = 1"),
            yaml_response("generate_code", code="second = 2"),
            yaml_response("analysis_complete", final_report="# 达到最大轮数后的报告"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", RecordingExecutor)

    agent = DataAnalysisAgent(
        llm_config=LLMConfig(
            api_key="offline-test-key",
            base_url="https://offline.invalid",
            model="fake-model",
        ),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=2,
        generate_word_report=False,
    )

    result = agent.analyze("验证最大轮数")

    assert result["total_rounds"] == 2
    assert len(result["analysis_results"]) == 2
    assert len(fake_llm.calls) == 3
    assert result["final_report"] == "# 达到最大轮数后的报告"


def test_multiple_session_directories_are_unique_and_have_contract_name(tmp_path):
    first = Path(create_session_output_dir(str(tmp_path), "同一个需求"))
    second = Path(create_session_output_dir(str(tmp_path), "同一个需求"))

    assert first.exists() and first.is_dir()
    assert second.exists() and second.is_dir()
    assert first != second
    assert re.fullmatch(r"session_[0-9a-f]{32}", first.name)
    assert re.fullmatch(r"session_[0-9a-f]{32}", second.name)
