"""Versioned Markdown templates for evidence-backed reports."""

from math import isfinite
import re

from data_analysis_agent.domain.enums import (
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)

from .models import ReportDocument
from .sanitize import safe_chart_reference, sanitize_markdown


class AnalysisReportTemplate:
    """Render the canonical Markdown representation of an analysis report."""

    version = "analysis-report-v1"

    def render_markdown(self, document: ReportDocument) -> str:
        """Return deterministic Markdown containing only verified report facts."""
        validation = document.evidence_validation
        unsupported_numbers = (
            validation.unsupported_numeric_claims if validation is not None else ()
        )
        narrative = sanitize_markdown(
            document.narrative_markdown,
            document=document,
            unsupported_numbers=unsupported_numbers,
        )
        metrics = tuple(
            metric
            for metric in document.metric_artifacts
            if metric.verification_status is EvidenceVerificationStatus.VERIFIED
        )
        charts = tuple(
            (chart, safe_chart_reference(chart.filename, document=document))
            for chart in document.chart_artifacts
        )
        charts = tuple((chart, reference) for chart, reference in charts if reference)
        has_warning = validation is not None and (
            not validation.valid
            or any(
                claim.status is EvidenceClaimStatus.PENDING_CONFIRMATION
                for claim in validation.claims
            )
        )

        if not metrics and not charts and not has_warning:
            return narrative

        blocks = [f"# {_markdown_text(document.title)}"]
        if narrative:
            blocks.append(narrative)
        if metrics:
            blocks.append(self._render_metrics(metrics))
        if charts:
            blocks.append(self._render_charts(charts))
        if has_warning:
            blocks.append(self._render_evidence_status(validation))
        return "\n\n".join(blocks)

    @staticmethod
    def _render_metrics(metrics: tuple) -> str:
        rows = [
            "## \u5173\u952e\u6307\u6807",
            "",
            "| \u6307\u6807 | \u503c | \u5355\u4f4d | \u6765\u6e90\u5217 |",
            "| --- | ---: | --- | --- |",
        ]
        for metric in metrics:
            value = format(metric.value, ".15g") if isfinite(metric.value) else ""
            rows.append(
                "| {} | {} | {} | {} |".format(
                    _markdown_table_cell(metric.name),
                    value,
                    _markdown_table_cell(metric.unit or ""),
                    _markdown_table_cell(", ".join(metric.source_columns)),
                )
            )
        return "\n".join(rows)

    @staticmethod
    def _render_charts(charts: tuple) -> str:
        rows = ["## \u56fe\u8868", ""]
        for chart, reference in charts:
            description = chart.title or chart.description or chart.filename
            rows.append(
                f"![{_markdown_text(description)}]({_markdown_image_target(reference)})"
            )
        return "\n".join(rows)

    @staticmethod
    def _render_evidence_status(validation) -> str:
        rows = ["## \u8bc1\u636e\u72b6\u6001", ""]
        if not validation.valid:
            rows.append("\u8bc1\u636e\u9a8c\u8bc1\u672a\u901a\u8fc7\u3002")
        pending_claims = tuple(
            claim
            for claim in validation.claims
            if claim.status is EvidenceClaimStatus.PENDING_CONFIRMATION
        )
        for claim in pending_claims:
            rows.append(f"- \u5f85\u786e\u8ba4\u58f0\u660e: {_markdown_text(claim.text)}")
        for code in validation.error_codes:
            rows.append(f"- {_markdown_text(code)}")
        if validation.unsupported_numeric_claims:
            rows.append("- \u5b58\u5728\u5f85\u786e\u8ba4\u6570\u5b57\u3002")
        if validation.missing_chart_ids:
            rows.append("- \u5b58\u5728\u65e0\u6cd5\u4f7f\u7528\u7684\u56fe\u8868\u3002")
        return "\n".join(rows)


def _markdown_text(value: str) -> str:
    """Keep generated Markdown on one line without allowing block injection."""
    return " ".join(value.replace("\r", "\n").splitlines()).strip()


def _markdown_table_cell(value: str) -> str:
    return _markdown_text(value).replace("|", "\\|")


def _markdown_image_target(value: str) -> str:
    """Use an angle destination when Markdown delimiters need protection."""
    return f"<{value}>" if re.search(r"[\s()]", value) else value
