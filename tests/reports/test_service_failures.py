from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.service import ReportService


class FailingRenderer:
    def __init__(self, report_format: ReportFormat, message: str) -> None:
        self.format = report_format
        self.message = message

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path) -> str:
        Path(output_path).write_text("partial", encoding="utf-8")
        raise RuntimeError(self.message)


class PlainRenderer:
    def __init__(self, report_format: ReportFormat) -> None:
        self.format = report_format

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path) -> str:
        Path(output_path).write_text(markdown, encoding="utf-8")
        return str(output_path)


@pytest.mark.parametrize("report_format", [ReportFormat.HTML, ReportFormat.DOCX])
def test_renderer_failure_keeps_markdown_and_cleans_its_temporary_file(
    tmp_path: Path, report_format: ReportFormat
):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    service = ReportService(renderers={report_format: FailingRenderer(report_format, "conversion failed")})

    bundle = service.generate(document, formats={ReportFormat.MARKDOWN, report_format})

    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is True
    assert results[report_format].generated is False
    assert "conversion failed" in results[report_format].error
    assert not list(tmp_path.glob(".*.tmp"))


def test_markdown_write_failure_does_not_prevent_html(tmp_path: Path, monkeypatch):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    original_write_text = Path.write_text

    def fail_markdown_temporary_write(path: Path, data: str, *args, **kwargs):
        if path.name.startswith(".最终分析报告.md."):
            raise OSError("disk unavailable")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_markdown_temporary_write)
    bundle = ReportService(renderers={ReportFormat.HTML: PlainRenderer(ReportFormat.HTML)}).generate(
        document, formats={ReportFormat.MARKDOWN, ReportFormat.HTML}
    )

    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is False
    assert results[ReportFormat.HTML].generated is True
    assert not list(tmp_path.glob(".*.tmp"))


def test_storage_failure_keeps_local_file_and_is_recorded(tmp_path: Path):
    class FailingStorage:
        def store_report(self, **kwargs):
            raise RuntimeError("api_key=super-secret E:\\host\\sensitive")

    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")

    bundle = ReportService(artifact_storage=FailingStorage()).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    result = bundle.results[0]
    assert result.generated is True
    assert Path(result.file_path).is_file()
    assert len(bundle.storage_errors) == 1
    assert "super-secret" not in bundle.storage_errors[0]
    assert "sensitive" not in bundle.storage_errors[0]


def test_unsupported_format_returns_failed_result_without_raising(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")

    bundle = ReportService(renderers={}).generate(document, formats={ReportFormat.HTML})

    result = bundle.results[0]
    assert result.generated is False
    assert result.error == "unsupported report format"
