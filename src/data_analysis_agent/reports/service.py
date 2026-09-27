"""Isolated orchestration for generating report files from canonical Markdown."""

from __future__ import annotations

from collections.abc import Collection, Mapping
import math
import os
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import uuid4

from data_analysis_agent.domain.errors import EvidenceErrorCode
from data_analysis_agent.domain.enums import EvidenceVerificationStatus, ReportFormat
from data_analysis_agent.domain.models import (
    EvidenceValidation,
    ReportArtifact,
)
from data_analysis_agent.services.errors import sanitize_exception

from .html import HtmlReportRenderer
from .models import ReportBundle, ReportDocument, ReportFormatResult
from .sanitize import sanitize_markdown
from .templates import AnalysisReportTemplate
from .word import WordReportRenderer


_NUMERIC_CLAIM_PATTERN = re.compile(r"(?<![\w.])-?(?:\d+(?:\.\d+)?|\.\d+)%?")


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
        allowed_output_root: str | Path | None = None,
    ) -> None:
        self.template = template if template is not None else AnalysisReportTemplate()
        self.renderers = dict(default_renderers() if renderers is None else renderers)
        self.artifact_storage = artifact_storage
        self.storage = storage
        self.evidence_registry = evidence_registry
        self.allowed_output_root = (
            Path(allowed_output_root).resolve(strict=False)
            if allowed_output_root is not None
            else None
        )

    def generate(
        self, document: ReportDocument, *, formats: Collection[ReportFormat]
    ) -> ReportBundle:
        template_version = self._validate_template_version(document)
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
                self._cleanup_temporary(temporary)
                results.append(self._failed_result(report_format, exc))
                continue
            results.append(
                self._store_success(report_format, target, document, storage_errors)
            )

        return ReportBundle(
            task_id=document.task_id,
            template_version=template_version,
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
        download_url = None
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
                if artifact is not None and self.storage is not None:
                    storage_uri = artifact.file_path
                    download_url = self.storage.create_download_url(storage_uri)
            except Exception as exc:
                storage_errors.append(self._safe_error(exc))
        return ReportFormatResult(
            format=report_format,
            generated=True,
            file_path=str(target),
            download_url=download_url,
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
        elif validation is None:
            validation = self._validate_document_evidence(document)
        if not isinstance(validation, EvidenceValidation) or validation.task_id != document.task_id:
            raise ValueError("evidence validation task does not match document task")
        return validation

    @classmethod
    def _validate_document_evidence(cls, document: ReportDocument) -> EvidenceValidation:
        text = cls._report_text_without_nonclaims(document.narrative_markdown)
        claims = tuple(cls._numeric_claims(text))
        verified_values = tuple(
            metric.value
            for metric in document.metric_artifacts
            if metric.verification_status is EvidenceVerificationStatus.VERIFIED
        )
        unsupported = tuple(
            token
            for token in claims
            if not cls._matches_verified_value(token, verified_values)
        )
        missing_charts = tuple(
            chart.artifact_id
            for chart in document.chart_artifacts
            if chart.verification_status is not EvidenceVerificationStatus.VERIFIED
        )
        error_codes: list[str] = []
        if unsupported:
            error_codes.append(EvidenceErrorCode.UNSUPPORTED_NUMERIC_CLAIM.value)
        if missing_charts:
            error_codes.append(EvidenceErrorCode.CHART_PATH_INVALID.value)
        return EvidenceValidation(
            task_id=document.task_id,
            valid=not unsupported and not missing_charts,
            unsupported_numeric_claims=unsupported,
            missing_chart_ids=missing_charts,
            error_codes=tuple(error_codes),
        )

    @staticmethod
    def _report_text_without_nonclaims(markdown: str) -> str:
        text = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"https?://\S+|www\.\S+", "", text)
        return re.sub(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            "",
            text,
        )

    @staticmethod
    def _numeric_claims(text: str) -> tuple[str, ...]:
        seen: list[str] = []
        for match in _NUMERIC_CLAIM_PATTERN.finditer(text):
            token = match.group(0)
            if token not in seen:
                seen.append(token)
        return tuple(seen)

    @staticmethod
    def _matches_verified_value(token: str, verified_values: tuple[float, ...]) -> bool:
        try:
            value = float(token.rstrip("%"))
        except ValueError:
            return False
        for verified_value in verified_values:
            if math.isclose(value, verified_value, rel_tol=1e-6, abs_tol=1e-9):
                return True
        return False

    def _validate_output_root(self, output_root: str) -> Path:
        if self.allowed_output_root is None:
            raise ValueError("trusted output root is required")
        try:
            root = Path(output_root).resolve(strict=True)
            allowed = self.allowed_output_root.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValueError("output root is unavailable") from exc
        if not root.is_dir():
            raise ValueError("output root must be an existing directory")
        if not allowed.is_dir():
            raise ValueError("trusted output root must be an existing directory")
        if allowed.parent == allowed:
            raise ValueError("trusted output root must be a task-owned directory")
        try:
            root.relative_to(allowed)
        except ValueError as exc:
            raise ValueError("output root is outside the trusted output root") from exc
        if root.parent == root:
            raise ValueError("output root must be a task-owned directory")
        if any((root / marker).exists() for marker in (".git", "pyproject.toml", "setup.py")):
            raise ValueError("output root must be a task-owned directory")
        return root

    @staticmethod
    def _cleanup_temporary(temporary: Path) -> None:
        """Remove only a temporary file; never recurse into an unexpected directory."""
        try:
            if temporary.is_file() or temporary.is_symlink():
                temporary.unlink(missing_ok=True)
        except Exception:
            # The renderer error is the actionable format failure. Cleanup is best effort.
            return

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
    def _safe_error(exc: BaseException) -> str:
        message = sanitize_exception(exc)
        return message[:240] or "report generation failed"

    def _validate_template_version(self, document: ReportDocument) -> str:
        template_version = getattr(self.template, "version", None)
        if not isinstance(template_version, str) or not template_version.strip():
            raise ValueError("template version is unavailable")
        if document.template_version != template_version:
            raise ValueError(
                "document template version does not match renderer template version"
            )
        return template_version


class _UnsupportedFormatError(Exception):
    pass
