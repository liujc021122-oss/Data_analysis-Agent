"""Isolated orchestration for generating report files from canonical Markdown."""

from __future__ import annotations

from collections.abc import Collection, Mapping
import os
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import uuid4

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import EvidenceValidation, ReportArtifact

from .html import HtmlReportRenderer
from .models import ReportBundle, ReportDocument, ReportFormatResult
from .sanitize import sanitize_markdown
from .templates import AnalysisReportTemplate
from .word import generate_word_report


class ReportRenderer(Protocol):
    """A replaceable renderer which writes one report format to ``output_path``."""

    format: ReportFormat

    def render(
        self, *, markdown: str, document: ReportDocument, output_path: Path
    ) -> str:
        ...


class MarkdownReportRenderer:
    """Write canonical Markdown without introducing a second transformation."""

    format = ReportFormat.MARKDOWN

    def render(
        self, *, markdown: str, document: ReportDocument, output_path: Path
    ) -> str:
        output_path.write_text(markdown, encoding="utf-8")
        return str(output_path)


class WordReportRenderer:
    """Local adapter around the legacy Word generator until a dedicated adapter exists."""

    format = ReportFormat.DOCX

    def render(
        self, *, markdown: str, document: ReportDocument, output_path: Path
    ) -> str:
        figures = [chart.model_dump(mode="python") for chart in document.chart_artifacts]
        return generate_word_report(
            markdown_content=markdown,
            output_path=output_path,
            session_output_dir=document.output_root,
            figures=figures,
        )


def default_renderers() -> dict[ReportFormat, ReportRenderer]:
    """Return all renderers available in a standard installation."""
    return {
        ReportFormat.MARKDOWN: MarkdownReportRenderer(),
        ReportFormat.HTML: HtmlReportRenderer(),
        ReportFormat.DOCX: WordReportRenderer(),
    }


class ReportService:
    """Generate requested report formats without allowing one format to affect another."""

    _FILENAMES = {
        ReportFormat.MARKDOWN: "最终分析报告.md",
        ReportFormat.HTML: "最终分析报告.html",
        ReportFormat.DOCX: "最终分析报告.docx",
    }
    _MIME_TYPES = {
        ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
        ReportFormat.HTML: "text/html; charset=utf-8",
        ReportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    def __init__(
        self,
        *,
        template: Any | None = None,
        renderers: Mapping[ReportFormat, ReportRenderer] | None = None,
        artifact_storage: Any | None = None,
        storage: Any | None = None,
        evidence_registry: Any | None = None,
    ) -> None:
        self.template = template or AnalysisReportTemplate()
        self.renderers = dict(default_renderers() if renderers is None else renderers)
        self.artifact_storage = artifact_storage or storage
        self.evidence_registry = evidence_registry

    def generate(
        self, document: ReportDocument, *, formats: Collection[ReportFormat]
    ) -> ReportBundle:
        root = self._validate_output_root(document.output_root)
        validation = self._validate_evidence(document)
        safe_document = document.model_copy(
            update={
                "narrative_markdown": sanitize_markdown(
                    document.narrative_markdown,
                    document=document,
                    unsupported_numbers=validation.unsupported_numeric_claims,
                ),
                "evidence_validation": validation,
            }
        )
        markdown = self.template.render_markdown(safe_document)
        results: list[ReportFormatResult] = []
        storage_errors: list[str] = []

        for report_format in self._requested_formats(formats):
            target = root / self._filename_for(report_format)
            temporary = root / f".{target.name}.{uuid4().hex}.tmp"
            try:
                self._write_format(
                    report_format,
                    markdown=markdown,
                    document=safe_document,
                    output_path=temporary,
                )
                os.replace(temporary, target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                results.append(self._failed_result(report_format, exc))
                continue
            results.append(
                self._store_success(report_format, target, document, storage_errors)
            )

        return ReportBundle(
            task_id=document.task_id,
            template_version=document.template_version,
            markdown_content=markdown,
            results=tuple(results),
            evidence_validation=validation,
            storage_errors=tuple(storage_errors),
        )

    def _write_format(
        self,
        report_format: ReportFormat,
        *,
        markdown: str,
        document: ReportDocument,
        output_path: Path,
    ) -> None:
        if report_format is ReportFormat.MARKDOWN:
            MarkdownReportRenderer().render(
                markdown=markdown, document=document, output_path=output_path
            )
            return
        renderer = self.renderers.get(report_format)
        if renderer is None:
            raise _UnsupportedFormatError()
        renderer.render(markdown=markdown, document=document, output_path=output_path)

    def _store_success(
        self,
        report_format: ReportFormat,
        target: Path,
        document: ReportDocument,
        storage_errors: list[str],
    ) -> ReportFormatResult:
        artifact = None
        if self.artifact_storage is not None:
            try:
                stored = self.artifact_storage.store_report(
                    task_id=document.task_id,
                    source_path=target,
                    filename=target.name,
                    mime_type=self._MIME_TYPES[report_format],
                    format=report_format,
                    title=document.title,
                )
                artifact = self._report_artifact(stored, report_format, target, document)
            except Exception as exc:
                storage_errors.append(self._safe_error(exc))
        return ReportFormatResult(
            format=report_format,
            generated=True,
            file_path=str(target),
            artifact=artifact,
        )

    @staticmethod
    def _report_artifact(
        stored: Any, report_format: ReportFormat, target: Path, document: ReportDocument
    ) -> ReportArtifact | None:
        if isinstance(stored, ReportArtifact):
            return stored
        candidate = stored[0] if isinstance(stored, tuple) and stored else stored
        if candidate is None:
            return None
        return ReportArtifact(
            artifact_id=getattr(candidate, "artifact_id", uuid4()),
            format=report_format,
            file_path=getattr(candidate, "file_path", str(target)),
            title=getattr(candidate, "title", document.title),
            content_hash=getattr(candidate, "content_hash", None),
            size_bytes=getattr(candidate, "size_bytes", target.stat().st_size),
        )

    def _validate_evidence(self, document: ReportDocument) -> EvidenceValidation:
        validation = document.evidence_validation
        if self.evidence_registry is not None:
            validation = self.evidence_registry.validate_report(
                document.narrative_markdown, document.task_id
            )
        validation = validation or EvidenceValidation(task_id=document.task_id, valid=True)
        if not isinstance(validation, EvidenceValidation) or validation.task_id != document.task_id:
            raise ValueError("evidence validation task does not match document task")
        return validation

    @staticmethod
    def _validate_output_root(output_root: str) -> Path:
        try:
            root = Path(output_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("output root is unavailable") from exc
        if not root.is_dir():
            raise ValueError("output root must be an existing directory")
        return root

    @classmethod
    def _requested_formats(cls, formats: Collection[ReportFormat]) -> list[ReportFormat]:
        try:
            return sorted(set(formats), key=lambda item: item.value)
        except (AttributeError, TypeError) as exc:
            raise ValueError("report formats must be ReportFormat values") from exc

    @classmethod
    def _filename_for(cls, report_format: ReportFormat) -> str:
        try:
            return cls._FILENAMES[report_format]
        except KeyError as exc:
            raise _UnsupportedFormatError() from exc

    @staticmethod
    def _failed_result(report_format: ReportFormat, exc: Exception) -> ReportFormatResult:
        error = "unsupported report format" if isinstance(exc, _UnsupportedFormatError) else ReportService._safe_error(exc)
        return ReportFormatResult(format=report_format, generated=False, error=error)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip()
        message = re.sub(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)\s*[:=]\s*\S+",
            r"\1=[redacted]",
            message,
        )
        message = re.sub(r"(?<!\w)[A-Za-z]:\\[^\s'\"]+", "[path]", message)
        message = re.sub(r"(?<!\w)/(?:[^\s'\"]+/)+[^\s'\"]*", "[path]", message)
        return message[:240] or "report generation failed"


class _UnsupportedFormatError(Exception):
    pass
