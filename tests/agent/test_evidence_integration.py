from pathlib import Path
from uuid import uuid4

from data_analysis_agent.agent.core import DataAnalysisAgent
from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.models import MetricArtifact
from data_analysis_agent.execution.agent_session import AgentExecutionSession
from data_analysis_agent.execution import ExecutionResult
from data_analysis_agent.services.evidence import EvidenceRegistry


class RecordingBackend:
    production_safe = True

    def execute(self, request):
        return ExecutionResult(
            success=True,
            stdout="container output",
            stderr="",
            exit_code=0,
            code_sha256=request.code_sha256,
            duration_ms=1,
        )


class FakePathResolver:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve_output_path(self, value):
        candidate = Path(value)
        if str(candidate).startswith("/output/"):
            candidate = self.root / str(candidate).removeprefix("/output/")
        elif not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(self.root)
        return resolved


def make_recording_session(tmp_path):
    return AgentExecutionSession(
        backend=RecordingBackend(),
        output_dir=tmp_path / "output",
        output_scope=tmp_path,
        task_id=uuid4(),
    )


def test_execution_audit_has_unique_execution_id(tmp_path):
    session = make_recording_session(tmp_path)
    try:
        first = session.execute_code("print('first')")
        second = session.execute_code("print('second')")
    finally:
        session.close()

    assert first["audit"]["execution_id"]
    assert first["audit"]["execution_id"] != second["audit"]["execution_id"]


def test_agent_registers_metric_and_chart_and_returns_evidence_snapshot(tmp_path):
    task_id = uuid4()
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"png")
    agent = object.__new__(DataAnalysisAgent)
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent._provided_evidence_registry = agent.evidence_registry
    agent.analysis_results = []
    agent.execution_audits = []
    agent._display_text = str
    agent.executor = FakePathResolver(tmp_path)

    metric = agent.register_metric(
        MetricArtifact(
            name="revenue",
            value=10.0,
            source_dataset_ids=(uuid4(),),
            task_id=task_id,
        )
    )
    assert metric.task_id == task_id
    collected = agent._handle_collect_figures(
        "",
        {
            "figures_to_collect": [
                {
                    "figure_number": 1,
                    "filename": "chart.png",
                    "file_path": str(chart_path),
                }
            ]
        },
    )

    assert collected["collected_figures"][0]["filename"] == "chart.png"
    snapshot = agent.evidence_registry.snapshot(task_id)
    assert len(snapshot["charts"]) == 1
    assert (
        snapshot["charts"][0].verification_status
        is EvidenceVerificationStatus.VERIFIED
    )


def test_final_report_prompt_contains_only_verified_metric_context(tmp_path):
    task_id = uuid4()
    agent = object.__new__(DataAnalysisAgent)
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent.current_round = 1
    agent.session_output_dir = tmp_path
    agent.analysis_results = []
    metric = agent.register_metric(
        MetricArtifact(
            task_id=task_id,
            name="revenue",
            value=10.0,
            formula="sum(revenue)",
            source_dataset_ids=(uuid4(),),
            execution_id=uuid4(),
            code_hash="c" * 64,
        )
    )
    agent.evidence_registry.verify_metric(metric.artifact_id, 10.0)

    prompt = agent._build_final_report_prompt([])

    assert "revenue" in prompt
    assert "10.0" in prompt
    assert "verified" in prompt.lower()
