from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import EvidenceValidation, ReportArtifact
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.service import ReportService


class RecordingRenderer:
    def __init__(self, report_format: ReportFormat) -> None:
        self.format = report_format
        self.markdown: list[str] = []

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path) -> str:
        self.markdown.append(markdown)
        Path(output_path).write_text(f"{self.format.value}: {markdown}", encoding="utf-8")
        return str(output_path)


def test_service_generates_markdown_and_html_from_one_canonical_content(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    renderer = RecordingRenderer(ReportFormat.HTML)

    bundle = ReportService(renderers={ReportFormat.HTML: renderer}).generate(
        document, formats={ReportFormat.MARKDOWN, ReportFormat.HTML}
    )

    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is True
    assert results[ReportFormat.HTML].generated is True
    assert bundle.markdown_content == "# 报告"
    assert renderer.markdown == [bundle.markdown_content]
    assert "报告" in Path(results[ReportFormat.HTML].file_path).read_text(encoding="utf-8")


def test_service_registers_successful_file_with_artifact_storage(tmp_path: Path):
    class FakeArtifactStorage:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def store_report(self, **kwargs):
            self.calls.append(kwargs)
            return ReportArtifact(format=ReportFormat.MARKDOWN, file_path="memory://report.md")

    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    storage = FakeArtifactStorage()

    bundle = ReportService(artifact_storage=storage).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    result = bundle.results[0]
    assert result.artifact is not None
    assert storage.calls[0]["format"] is ReportFormat.MARKDOWN
    assert storage.calls[0]["mime_type"] == "text/markdown; charset=utf-8"


def test_service_uses_evidence_registry_before_canonical_rendering(tmp_path: Path):
    class EvidenceRegistry:
        def validate_report(self, narrative: str, task_id):
            return EvidenceValidation(
                task_id=task_id,
                valid=False,
                unsupported_numeric_claims=("999",),
            )

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="结论是 999"
    )

    bundle = ReportService(evidence_registry=EvidenceRegistry()).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert "999" not in bundle.markdown_content
    assert "【待确认数字】" in bundle.markdown_content


def test_service_rejects_missing_output_root_before_rendering(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path / "does-not-exist"), narrative_markdown="# 报告"
    )

    with pytest.raises(ValueError, match="output root"):
        ReportService().generate(document, formats={ReportFormat.MARKDOWN})
