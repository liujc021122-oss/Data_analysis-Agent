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


class DirectoryFailingRenderer:
    def __init__(self, report_format: ReportFormat) -> None:
        self.format = report_format

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path) -> str:
        output_path.mkdir()
        raise RuntimeError("directory conversion failed")


@pytest.mark.parametrize("report_format", [ReportFormat.HTML, ReportFormat.DOCX])
def test_renderer_failure_keeps_markdown_and_cleans_its_temporary_file(
    tmp_path: Path, report_format: ReportFormat
):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    service = ReportService(
        allowed_output_root=tmp_path,
        renderers={report_format: FailingRenderer(report_format, "conversion failed")},
    )

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
    bundle = ReportService(
        allowed_output_root=tmp_path,
        renderers={ReportFormat.HTML: PlainRenderer(ReportFormat.HTML)},
    ).generate(
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

    bundle = ReportService(
        allowed_output_root=tmp_path, artifact_storage=FailingStorage()
    ).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    result = bundle.results[0]
    assert result.generated is True
    assert Path(result.file_path).is_file()
    assert len(bundle.storage_errors) == 1
    assert "super-secret" not in bundle.storage_errors[0]
    assert "sensitive" not in bundle.storage_errors[0]


def test_download_url_failure_keeps_local_report_and_sanitizes_error(tmp_path: Path):
    class ArtifactStorage:
        def store_report(self, **kwargs):
            from data_analysis_agent.domain.models import ReportArtifact

            return ReportArtifact(
                format=ReportFormat.MARKDOWN,
                file_path="memory://report.md",
            )

    class FailingURLStorage:
        def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
            raise RuntimeError(
                "Authorization: Bearer download-secret C:\\private\\report.docx"
            )

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path,
        artifact_storage=ArtifactStorage(),
        storage=FailingURLStorage(),
    ).generate(document, formats={ReportFormat.MARKDOWN})

    result = bundle.results[0]
    assert result.generated is True
    assert result.download_url is None
    assert Path(result.file_path).is_file()
    assert len(bundle.storage_errors) == 1
    assert "download-secret" not in bundle.storage_errors[0]
    assert "report.docx" not in bundle.storage_errors[0]


def test_unsupported_format_returns_failed_result_without_raising(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")

    bundle = ReportService(allowed_output_root=tmp_path, renderers={}).generate(
        document, formats={ReportFormat.HTML}
    )

    result = bundle.results[0]
    assert result.generated is False
    assert result.error == "unsupported report format"


def test_cleanup_failure_does_not_escape_format_isolation(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path,
        renderers={ReportFormat.HTML: DirectoryFailingRenderer(ReportFormat.HTML)},
    ).generate(document, formats={ReportFormat.MARKDOWN, ReportFormat.HTML})

    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.HTML].generated is False
    assert results[ReportFormat.HTML].error is not None
    assert "directory conversion failed" in results[ReportFormat.HTML].error
    assert results[ReportFormat.MARKDOWN].generated is True
    assert list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "message, secret",
    (
        ("Authorization: Bearer top-secret-token", "top-secret-token"),
        ("https://user:password@example.com/private/report", "user:password"),
        ('token="quoted-secret"', "quoted-secret"),
        (r"\\private-server\secret-share\report.txt", "private-server"),
        (r"C:\private\secret.txt", "secret.txt"),
        ("/var/private/report.txt", "report.txt"),
    ),
)
def test_report_errors_redact_credentials_and_host_paths(
    tmp_path: Path, message: str, secret: str
):
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )
    service = ReportService(
        allowed_output_root=tmp_path,
        renderers={ReportFormat.HTML: FailingRenderer(ReportFormat.HTML, message)},
    )

    bundle = service.generate(document, formats={ReportFormat.HTML})

    error = bundle.results[0].error or ""
    assert secret not in error
