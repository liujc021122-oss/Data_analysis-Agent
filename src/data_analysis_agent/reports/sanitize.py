"""Deterministic safety filtering for report Markdown."""

from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import urlparse

from data_analysis_agent.domain.enums import EvidenceVerificationStatus

from .models import ReportDocument


_HTML_TAG_PATTERN = re.compile(r"<[^>]*>")
_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^\s)]+)(?:\s+[^)]*)?\)")
_LINK_PATTERN = re.compile(r"(?<!!)\[([^\]]*)\]\(([^\s)]+)(?:\s+[^)]*)?\)")


def safe_link_target(target: str) -> str | None:
    """Return a permitted external link target, or reject it."""
    parsed = urlparse(target.strip())
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return target.strip()
    if parsed.scheme or target.strip().startswith(("/", "\\\\")):
        return None
    return None


def safe_chart_reference(target: str, *, document: ReportDocument) -> str | None:
    """Return a verified chart filename only when its file is safely available."""
    output_root = Path(document.output_root).resolve()
    for chart in document.chart_artifacts:
        if (
            chart.filename != target
            or chart.task_id != document.task_id
            or chart.verification_status is not EvidenceVerificationStatus.VERIFIED
        ):
            continue
        try:
            chart_path = Path(chart.file_path).resolve(strict=False)
            chart_path.relative_to(output_root)
            if not chart_path.is_file():
                continue
            filename_path = Path(chart.filename)
            windows_filename = PureWindowsPath(chart.filename)
            if (
                filename_path.is_absolute()
                or windows_filename.is_absolute()
                or windows_filename.drive
                or chart.filename.startswith(("/", "\\"))
            ):
                continue
            safe_filename = (output_root / filename_path).resolve(strict=False)
            relative_filename = safe_filename.relative_to(output_root)
        except (OSError, ValueError):
            continue
        return relative_filename.as_posix()
    return None


def sanitize_markdown(
    markdown: str,
    *,
    document: ReportDocument,
    unsupported_numbers: Sequence[str] = (),
) -> str:
    """Sanitize Markdown while preserving fenced code blocks unchanged."""
    sanitized_lines: list[str] = []
    non_code_lines: list[str] = []
    in_code_block = False

    def flush_non_code_lines() -> None:
        if non_code_lines:
            sanitized_lines.append(
                _sanitize_non_code_line(
                    "\n".join(non_code_lines), document, unsupported_numbers
                )
            )
            non_code_lines.clear()

    for raw_line in markdown.replace("\r\n", "\n").split("\n"):
        if raw_line.strip().startswith("```"):
            flush_non_code_lines()
            in_code_block = not in_code_block
            sanitized_lines.append(raw_line)
            continue
        if in_code_block:
            sanitized_lines.append(raw_line)
            continue
        non_code_lines.append(raw_line)
    flush_non_code_lines()
    return "\n".join(sanitized_lines)


def _sanitize_non_code_line(
    line: str,
    document: ReportDocument,
    unsupported_numbers: Sequence[str],
) -> str:
    line = _HTML_TAG_PATTERN.sub("", line)
    protected_spans: list[str] = []

    def protect(value: str) -> str:
        token = f"\x00{len(protected_spans)}\x00"
        protected_spans.append(value)
        return token

    def sanitize_image(match: re.Match[str]) -> str:
        description, target = match.groups()
        chart_reference = safe_chart_reference(target, document=document)
        if chart_reference is None:
            return protect(f"[图片不可用: {description}]")
        return protect(f"![{description}]({chart_reference})")

    def sanitize_link(match: re.Match[str]) -> str:
        text, target = match.groups()
        safe_target = safe_link_target(target)
        if safe_target is None:
            return protect(text)
        return protect(f"[{text}]({safe_target})")

    line = _IMAGE_PATTERN.sub(sanitize_image, line)
    line = _LINK_PATTERN.sub(sanitize_link, line)
    for token in sorted(set(unsupported_numbers), key=len, reverse=True):
        if token:
            line = line.replace(token, "【待确认数字】")
    for index, value in enumerate(protected_spans):
        line = line.replace(f"\x00{index}\x00", value)
    return line
