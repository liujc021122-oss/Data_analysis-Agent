import inspect
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from data_analysis_agent.agent.core import DataAnalysisAgent
from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import ReportArtifact
from data_analysis_agent.reports.html import HtmlReportRenderer
from data_analysis_agent.reports.service import ReportService
from data_analysis_agent.services.evidence import EvidenceRegistry
from tests.fixtures.fake_llm import FakeLLM, yaml_response


def make_compatibility_agent(tmp_path, *, generate_word_report):
    task_id = uuid4()
    agent = object.__new__(DataAnalysisAgent)
    agent.session_output_dir = str(tmp_path)
    agent.base_output_dir = str(tmp_path)
    agent.analysis_results = []
    agent.current_round = 1
    agent.conversation_history = []
    agent.generate_word_report = generate_word_report
    agent.config = SimpleNamespace(api_key="", base_url="", max_tokens=128)
    agent.llm = FakeLLM([yaml_response("analysis_complete", final_report="# 报告")])
    agent.llm_port = SimpleNamespace(request_report=lambda **kwargs: "# 报告")
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent.artifact_records = []
    agent.storage = None
    agent.artifact_storage = None
    return agent


def test_agent_delegates_formats_to_report_service_and_keeps_legacy_fields(tmp_path):
    calls = []
    markdown_artifact = ReportArtifact(
        format=ReportFormat.MARKDOWN,
        file_path="memory://report.md",
    )
    html_artifact = ReportArtifact(
        format=ReportFormat.HTML,
        file_path="memory://report.html",
    )

    class FakeReportService:
        def generate(self, document, *, formats):
            calls.append((document, set(formats)))
            return SimpleNamespace(
                markdown_content="# 报告",
                results=(
                    SimpleNamespace(
                        format=ReportFormat.MARKDOWN,
                        generated=True,
                        file_path=str(tmp_path / "最终分析报告.md"),
                        download_url="download://report.md",
                        content_url="/api/artifacts/markdown/content",
                        artifact=markdown_artifact,
                        error=None,
                    ),
                    SimpleNamespace(
                        format=ReportFormat.HTML,
                        generated=True,
                        file_path=str(tmp_path / "最终分析报告.html"),
                        download_url="download://report.html",
                        content_url="/api/artifacts/html/content",
                        artifact=html_artifact,
                        error=None,
                    ),
                ),
                storage_errors=(),
                evidence_validation=SimpleNamespace(
                    model_dump=lambda mode="json": {"valid": True}
                ),
            )

    agent = make_compatibility_agent(tmp_path, generate_word_report=False)
    agent.report_service = FakeReportService()

    result = agent._generate_final_report()

    assert len(calls) == 1
    assert ReportFormat.MARKDOWN in calls[0][1]
    assert ReportFormat.HTML in calls[0][1]
    assert result["final_report"] == "# 报告"
    assert result["report_file_path"] == str(tmp_path / "最终分析报告.md")
    assert result["report_download_url"] == "download://report.md"
    assert result["report_content_url"] == "/api/artifacts/markdown/content"
    assert result["html_report_file_path"] == str(tmp_path / "最终分析报告.html")
    assert result["html_report_generated"] is True
    assert result["html_report_error"] is None
    assert result["html_report_download_url"] == "download://report.html"
    assert result["html_report_content_url"] == "/api/artifacts/html/content"
    assert len(result["report_results"]) == 2
    assert result["report_results"][0]["content_url"] == "/api/artifacts/markdown/content"
    assert len(result["report_artifacts"]) == 2


def test_agent_wires_trusted_session_root_into_report_service(tmp_path):
    agent = make_compatibility_agent(tmp_path, generate_word_report=False)

    service = agent._get_report_service()

    assert Path(service.allowed_output_root).resolve() == tmp_path.resolve()


def test_report_service_constructor_parameter_is_keyword_only_and_last():
    parameters = list(inspect.signature(DataAnalysisAgent).parameters.values())

    assert parameters[-1].name == "report_service"
    assert parameters[-1].kind is inspect.Parameter.KEYWORD_ONLY


def test_word_failure_preserves_markdown_and_exposes_legacy_error(tmp_path):
    class FailingDocxRenderer:
        format = ReportFormat.DOCX

        def render(self, *, markdown, document, output_path):
            raise RuntimeError("baseline Word failure")

    agent = make_compatibility_agent(tmp_path, generate_word_report=True)
    agent.report_service = ReportService(
        allowed_output_root=tmp_path,
        renderers={
            ReportFormat.DOCX: FailingDocxRenderer(),
            ReportFormat.HTML: HtmlReportRenderer(),
        }
    )

    result = agent._generate_final_report()

    assert Path(result["report_file_path"]).exists()
    assert result["word_report_generated"] is False
    assert result["word_report_file_path"] == str(tmp_path / "最终分析报告.docx")
    assert "baseline Word failure" in result["word_report_error"]
