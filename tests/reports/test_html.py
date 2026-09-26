from pathlib import Path
import re
from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import EvidenceVerificationStatus, ReportFormat
from data_analysis_agent.domain.models import (
    ChartArtifact,
    EvidenceValidation,
    MetricArtifact,
)
from data_analysis_agent.reports.html import HtmlReportRenderer
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.sanitize import sanitize_markdown
from data_analysis_agent.reports.templates import AnalysisReportTemplate


def test_default_template_injects_verified_metric_and_chart(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "trend.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="# \u53d9\u8ff0\n\n\u6b63\u6587",
        metric_artifacts=(
            MetricArtifact(
                task_id=task_id,
                name="revenue",
                value=10.0,
                source_dataset_ids=(uuid4(),),
                execution_id=uuid4(),
                code_hash="a" * 64,
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="trend.png",
                file_path=str(chart),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    markdown = AnalysisReportTemplate().render_markdown(document)

    assert AnalysisReportTemplate.version == "analysis-report-v1"
    assert "revenue" in markdown
    assert "10" in markdown
    assert "![" in markdown


def test_template_preserves_sanitized_narrative_without_evidence(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        narrative_markdown="# \u65e7\u62a5\u544a\n\n[\u94fe\u63a5](javascript:alert(1))",
    )

    assert AnalysisReportTemplate().render_markdown(document) == sanitize_markdown(
        document.narrative_markdown, document=document
    )


def test_template_uses_only_verified_metrics_and_safe_charts(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "trend.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        metric_artifacts=(
            MetricArtifact(
                task_id=task_id,
                name="verified",
                value=1.2345678901234567,
                unit="USD",
                source_columns=("amount",),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
            MetricArtifact(
                task_id=task_id,
                name="unverified",
                value=999.0,
            ),
        ),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="trend.png",
                file_path=str(chart),
                title="\u8d8b\u52bf",
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
            ChartArtifact(
                task_id=task_id,
                filename="missing.png",
                file_path=str(tmp_path / "missing.png"),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    markdown = AnalysisReportTemplate().render_markdown(document)

    assert "1.23456789012346" in markdown
    assert "USD" in markdown
    assert "amount" in markdown
    assert "unverified" not in markdown
    assert "999" not in markdown
    assert "![\u8d8b\u52bf](trend.png)" in markdown
    assert "missing.png" not in markdown


def test_template_adds_evidence_status_for_invalid_validation(tmp_path: Path):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="\u53d9\u8ff0\u4e2d\u6709 999",
        evidence_validation=EvidenceValidation(
            task_id=task_id,
            valid=False,
            unsupported_numeric_claims=("999",),
            error_codes=("UNSUPPORTED_NUMERIC_CLAIM",),
        ),
    )

    markdown = AnalysisReportTemplate().render_markdown(document)

    assert "\u3010\u5f85\u786e\u8ba4\u6570\u5b57\u3011" in markdown
    assert "## \u8bc1\u636e\u72b6\u6001" in markdown
    assert "UNSUPPORTED_NUMERIC_CLAIM" in markdown


def test_html_renderer_escapes_text_and_preserves_report_structure(tmp_path: Path):
    output = tmp_path / "report.html"
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        narrative_markdown="# \u6807\u9898\n\n- \u9879\u76ee",
    )

    HtmlReportRenderer().render(
        markdown=AnalysisReportTemplate().render_markdown(document),
        document=document,
        output_path=output,
    )

    html = output.read_text(encoding="utf-8")
    assert "<h1>" in html
    assert "<li>\u9879\u76ee</li>" in html
    assert "<script" not in html
    assert '<meta charset="utf-8">' in html


def test_html_renderer_accepts_only_safe_links_and_current_task_images(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "trend.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="trend.png",
                file_path=str(chart),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )
    output = tmp_path / "report.html"

    renderer = HtmlReportRenderer()
    assert renderer.format is ReportFormat.HTML
    renderer.render(
        markdown=(
            "<script>alert(1)</script>\n\n"
            "[safe <link>](https://example.com/?q=<value>) "
            "[bad](file:///secret)\n\n"
            "![\u8d8b\u52bf <img>](trend.png) ![\u4e22\u5931](missing.png)"
        ),
        document=document,
        output_path=output,
    )

    html = output.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'href="https://example.com/?q=&lt;value&gt;"' in html
    assert "file:///secret" not in html
    assert 'src="trend.png"' in html
    assert "[\u56fe\u7247\u4e0d\u53ef\u7528: \u4e22\u5931]" in html


@pytest.mark.parametrize("filename", ("space chart.png", "chart)name.png"))
def test_template_and_html_render_verified_chart_markdown_delimiters(
    tmp_path: Path, filename: str
):
    task_id = uuid4()
    chart = tmp_path / filename
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename=filename,
                file_path=str(chart),
                title="verified chart",
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )
    output = tmp_path / "report.html"

    markdown = AnalysisReportTemplate().render_markdown(document)
    HtmlReportRenderer().render(markdown=markdown, document=document, output_path=output)

    rendered = output.read_text(encoding="utf-8")
    assert f'<img src="{filename}" alt="verified chart">' in rendered

    HtmlReportRenderer().render(
        markdown=f"![missing](<{filename}.missing>)",
        document=document,
        output_path=output,
    )
    assert "[\u56fe\u7247\u4e0d\u53ef\u7528: missing]" in output.read_text(encoding="utf-8")


def test_html_metric_table_honors_escaped_pipes_and_preserves_value_column(tmp_path: Path):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        metric_artifacts=(
            MetricArtifact(
                task_id=task_id,
                name="left|right",
                value=1.2345678901234567,
                unit="USD|month",
                source_columns=("source|column",),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )
    output = tmp_path / "report.html"

    markdown = AnalysisReportTemplate().render_markdown(document)
    HtmlReportRenderer().render(markdown=markdown, document=document, output_path=output)

    rendered = output.read_text(encoding="utf-8")
    row = re.search(r"<tbody><tr>(.*?)</tr></tbody>", rendered)
    assert row is not None
    cells = re.findall(r"<td>(.*?)</td>", row.group(1))
    assert cells == ["left|right", "1.23456789012346", "USD|month", "source|column"]
