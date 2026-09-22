import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from pydantic import BaseModel

from data_analysis_agent.agent.core import DataAnalysisAgent
from data_analysis_agent.agent.legacy_adapter import LegacyAnalysisAdapter
from data_analysis_agent.agent.orchestrator import AgentOrchestrator
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.domain.enums import TaskStatus
from tests.fixtures.fake_llm import FakeLLM, yaml_response
from tests.fixtures.recording_executor import RecordingExecutor


class _ReportPayload(BaseModel):
    report_id: UUID
    score: float


def test_adapter_keeps_execution_failure_as_feedback_for_the_next_model_step(tmp_path, monkeypatch):
    fake_llm = FakeLLM([
        yaml_response("generate_code", code="raise ValueError('planned failure')"),
        yaml_response("generate_code", code="value = 2 + 3"),
        yaml_response("analysis_complete", final_report="# analysis marker"),
        yaml_response("analysis_complete", final_report="# final report"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    agent = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=5,
        generate_word_report=False,
    )
    agent.session_output_dir = str(tmp_path)
    agent.executor = RecordingExecutor()
    agent.conversation_history = [{"role": "user", "content": "offline"}]
    agent.analysis_results = []
    agent.current_round = 0

    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=5,
    )
    handlers = adapter.handlers()
    orchestrator = AgentOrchestrator(
        task=adapter.task,
        handlers=handlers,
        limits=adapter.limits,
    )

    result = orchestrator.run()

    assert result.status is TaskStatus.COMPLETED
    assert len(agent.analysis_results) == 2
    assert agent.analysis_results[0]["result"]["success"] is False
    assert "planned failure" in agent.conversation_history[-1]["content"]


def test_adapter_maps_report_generation_to_one_reporting_stage_call(monkeypatch):
    calls = []
    agent = object.__new__(DataAnalysisAgent)
    agent.analysis_results = []
    agent.conversation_history = []
    agent.current_round = 0
    agent.generate_word_report = False
    agent._generate_final_report = lambda: calls.append("report") or {"final_report": "# done"}
    adapter = LegacyAnalysisAdapter(agent=agent, user_input="offline", dataset_context=[], max_rounds=0)

    result = AgentOrchestrator(
        task=adapter.task,
        handlers=adapter.handlers(),
        limits=adapter.limits,
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert calls == ["report"]
    assert result.output["final_report"] == "# done"


def test_adapter_zero_rounds_skips_analysis_but_generates_report(monkeypatch):
    fake_llm = FakeLLM([yaml_response("analysis_complete", final_report="# report")])
    agent = object.__new__(DataAnalysisAgent)
    agent.llm = fake_llm
    agent.analysis_results = []
    agent.conversation_history = [{"role": "user", "content": "offline"}]
    agent.current_round = 0
    agent.generate_word_report = False
    agent._generate_final_report = lambda: {"final_report": "# report"}

    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=0,
    )
    result = AgentOrchestrator(
        task=adapter.task,
        handlers=adapter.handlers(),
        limits=adapter.limits,
    ).run()

    assert result.status is TaskStatus.COMPLETED
    assert fake_llm.calls == []
    assert result.output["final_report"] == "# report"


def test_adapter_maps_action_serialization_failure_to_safe_model_error():
    class BrokenAction:
        def model_dump_json(self):
            raise RuntimeError("api_key=secret C:\\private\\prompt.json")

    agent = object.__new__(DataAnalysisAgent)
    agent.analysis_results = []
    agent.conversation_history = []
    agent.current_round = 0
    agent.max_rounds = 1
    agent.executor = RecordingExecutor()
    agent._build_conversation_prompt = lambda: "offline"
    agent._request_structured_action = lambda **kwargs: BrokenAction()
    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=1,
    )

    result = AgentOrchestrator(
        task=adapter.task,
        handlers=adapter.handlers(),
        limits=adapter.limits,
    ).run()

    assert result.status is TaskStatus.FAILED
    error_event = next(
        event for event in result.state.events if event.event_type.value == "ERROR"
    )
    assert error_event.metadata["cause_code"] == "MODEL_ERROR"
    assert "secret" not in result.error_message
    assert "private" not in result.error_message


def test_to_legacy_result_normalizes_report_and_agent_values_for_strict_json():
    task_id = UUID("11111111-1111-1111-1111-111111111111")
    report_payload = _ReportPayload(report_id=task_id, score=float("nan"))
    opaque_value = object()
    agent = object.__new__(DataAnalysisAgent)
    agent.analysis_results = [
        {
            "payload": report_payload,
            "run_id": task_id,
            "score": float("inf"),
            "opaque": opaque_value,
        }
    ]
    agent.conversation_history = [
        {"role": "assistant", "content": report_payload, "score": float("-inf")}
    ]
    agent.current_round = 1
    agent.session_output_dir = Path("outputs")
    agent.task_id = task_id
    agent.artifact_records = [report_payload]
    agent.storage_error = opaque_value
    agent._generate_final_report = lambda: {
        "final_report": report_payload,
        "report_id": task_id,
        "report_score": float("nan"),
        "report_path": Path("report.md"),
    }

    adapter = LegacyAnalysisAdapter(
        agent=agent,
        user_input="offline",
        dataset_context=[],
        max_rounds=0,
    )
    report_result = adapter._report(None, None)
    payload = adapter.to_legacy_result(
        SimpleNamespace(output=report_result.output, task_id=task_id)
    )

    json.dumps(payload, allow_nan=False)

    safe_model = {"report_id": str(task_id), "score": "nan"}
    assert payload["final_report"] == safe_model
    assert payload["report_id"] == str(task_id)
    assert payload["report_score"] == "nan"
    assert payload["report_path"] == "report.md"
    assert payload["analysis_results"][0]["payload"] == safe_model
    assert payload["analysis_results"][0]["run_id"] == str(task_id)
    assert payload["analysis_results"][0]["score"] == "inf"
    assert payload["analysis_results"][0]["opaque"] == str(opaque_value)
    assert payload["conversation_history"][0]["content"] == safe_model
    assert payload["conversation_history"][0]["score"] == "-inf"
    assert payload["artifact_records"][0] == safe_model
    assert payload["session_output_dir"] == "outputs"
    assert payload["task_id"] == str(task_id)
    assert payload["storage_error"] == str(opaque_value)
