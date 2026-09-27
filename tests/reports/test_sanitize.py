from pathlib import Path
from uuid import uuid4

from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.models import ChartArtifact, MetricArtifact
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.sanitize import (
    safe_chart_reference,
    safe_link_target,
    sanitize_markdown,
)


def test_sanitizer_escapes_raw_html_and_rejects_dangerous_links(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path))
    cleaned = sanitize_markdown(
        "<script>alert(1)</script> [坏链接](javascript:alert(1)) [安全](https://example.com)",
        document=document,
    )
    assert "<script>" not in cleaned
    assert "javascript:" not in cleaned
    assert "https://example.com" in cleaned
    assert safe_link_target("file:///secret.txt") is None


def test_sanitizer_removes_multiline_raw_html_outside_code_blocks(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path))

    cleaned = sanitize_markdown("<script\n>alert(1)</script>", document=document)

    assert "<script" not in cleaned.lower()


def test_sanitizer_only_keeps_verified_current_task_chart(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "chart.png"
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
    cleaned = sanitize_markdown(
        "![合法](trend.png) ![越界](../outside.png) ![外部](https://x.test/a.png)",
        document=document,
    )
    assert "chart.png" in cleaned
    assert "trend.png" not in cleaned
    assert "https://x.test/a.png" not in cleaned
    assert "图片不可用" in cleaned


def test_unsupported_numeric_tokens_are_marked_for_confirmation(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        metric_artifacts=(MetricArtifact(name="revenue", value=10.0, task_id=None),),
    )
    cleaned = sanitize_markdown(
        "收入是999，增长率为12.5%",
        document=document,
        unsupported_numbers=("999", "12.5%"),
    )
    assert "待确认" in cleaned
    assert "999" not in cleaned


def test_sanitizer_keeps_code_and_link_spans_unchanged(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path))

    cleaned = sanitize_markdown(
        "报告数字999 [版本7](https://example.com/7)\n"
        "```python\n<script>999</script>\n```",
        document=document,
        unsupported_numbers=("999", "7"),
    )

    assert "报告数字【待确认数字】" in cleaned
    assert "[版本7](https://example.com/7)" in cleaned
    assert "<script>999</script>" in cleaned


def test_safe_chart_reference_rejects_verified_directory(tmp_path: Path):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="directory.png",
                file_path=str(tmp_path),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    assert safe_chart_reference("directory.png", document=document) is None


def test_sanitizer_rejects_chart_filenames_outside_output_root(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "actual.png"
    chart.write_bytes(b"png")

    outside = tmp_path.parent / "outside.png"
    for filename in ("../outside.png", r"C:\outside.png"):
        document = ReportDocument(
            task_id=task_id,
            output_root=str(tmp_path),
            chart_artifacts=(
                ChartArtifact(
                    task_id=task_id,
                    filename=filename,
                    file_path=str(outside),
                    verification_status=EvidenceVerificationStatus.VERIFIED,
                ),
            ),
        )

        cleaned = sanitize_markdown(f"![chart]({filename})", document=document)

        assert cleaned == "[图片不可用: chart]"


def test_chart_reference_uses_verified_nested_file_path_not_display_name(tmp_path: Path):
    task_id = uuid4()
    nested = tmp_path / "nested"
    nested.mkdir()
    actual = nested / "actual chart (final).png"
    actual.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="display-name.png",
                file_path=str(actual),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    assert safe_chart_reference("display-name.png", document=document) == (
        "nested/actual chart (final).png"
    )
    cleaned = sanitize_markdown(
        "![display](display-name.png)", document=document
    )
    assert "nested/actual chart (final).png" in cleaned
    assert "![display](display-name.png)" not in cleaned


def test_chart_reference_rejects_unknown_target_when_a_verified_chart_exists(
    tmp_path: Path,
):
    task_id = uuid4()
    chart = tmp_path / "actual.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(
            ChartArtifact(
                task_id=task_id,
                filename="display.png",
                file_path=str(chart),
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    assert safe_chart_reference("unknown.png", document=document) is None
    assert sanitize_markdown("![unknown](unknown.png)", document=document) == (
        "[图片不可用: unknown]"
    )
