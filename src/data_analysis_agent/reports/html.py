"""Safe HTML rendering for the canonical report Markdown subset."""

import html
from pathlib import Path
import re

from data_analysis_agent.domain.enums import ReportFormat

from .models import ReportDocument
from .sanitize import safe_chart_reference, safe_link_target


_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_UNORDERED = re.compile(r"^\s*[-*+]\s+(.+)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_IMAGE = re.compile(
    r"!\[(?P<label>[^\]]*)\]\((?:<(?P<angle_target>[^>\r\n]*)>|"
    r"(?P<plain_target>[^\s)]+))(?:\s+[^)]*)?\)"
)
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^\s)]+)(?:\s+[^)]*)?\)")


class HtmlReportRenderer:
    """Render only the report Markdown subset into a complete HTML document."""

    format = ReportFormat.HTML

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path | str) -> str:
        body = self._render_blocks(markdown.replace("\r\n", "\n").replace("\r", "\n"), document)
        page = "\n".join(
            (
                "<!doctype html>",
                '<html lang="zh-CN">',
                "<head>",
                '<meta charset="utf-8">',
                f"<title>{html.escape(document.title, quote=True)}</title>",
                "</head>",
                "<body>",
                body,
                "</body>",
                "</html>",
            )
        )
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(page, encoding="utf-8")
        return str(destination)

    def _render_blocks(self, markdown: str, document: ReportDocument) -> str:
        lines = markdown.split("\n")
        output: list[str] = []
        paragraph: list[str] = []
        index = 0

        def flush_paragraph() -> None:
            if paragraph:
                output.append(f"<p>{self._inline(' '.join(paragraph), document)}</p>")
                paragraph.clear()

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                flush_paragraph()
                index += 1
                continue
            if stripped.startswith("```"):
                flush_paragraph()
                code: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].strip().startswith("```"):
                    code.append(lines[index])
                    index += 1
                if index < len(lines):
                    index += 1
                output.append(f"<pre><code>{html.escape(chr(10).join(code), quote=True)}</code></pre>")
                continue
            heading = _HEADING.match(stripped)
            if heading:
                flush_paragraph()
                level = len(heading.group(1))
                output.append(f"<h{level}>{self._inline(heading.group(2), document)}</h{level}>")
                index += 1
                continue
            if self._is_table_start(lines, index):
                flush_paragraph()
                table, index = self._render_table(lines, index, document)
                output.append(table)
                continue
            unordered = _UNORDERED.match(line)
            ordered = _ORDERED.match(line)
            if unordered or ordered:
                flush_paragraph()
                pattern = _UNORDERED if unordered else _ORDERED
                tag = "ul" if unordered else "ol"
                items: list[str] = []
                while index < len(lines):
                    match = pattern.match(lines[index])
                    if not match:
                        break
                    items.append(f"<li>{self._inline(match.group(1), document)}</li>")
                    index += 1
                output.append(f"<{tag}>{''.join(items)}</{tag}>")
                continue
            paragraph.append(stripped)
            index += 1
        flush_paragraph()
        return "\n".join(output)

    @staticmethod
    def _is_table_start(lines: list[str], index: int) -> bool:
        return (
            index + 1 < len(lines)
            and "|" in lines[index]
            and bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[index + 1]))
        )

    def _render_table(self, lines: list[str], index: int, document: ReportDocument) -> tuple[str, int]:
        headers = self._table_cells(lines[index])
        index += 2
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            rows.append(self._table_cells(lines[index]))
            index += 1
        head = "".join(f"<th>{self._inline(cell, document)}</th>" for cell in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{self._inline(cell, document)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>", index

    @staticmethod
    def _table_cells(line: str) -> list[str]:
        content = line.strip()
        if content.startswith("|"):
            content = content[1:]
        if content.endswith("|") and not content.endswith("\\|"):
            content = content[:-1]

        cells: list[str] = []
        cell: list[str] = []
        index = 0
        while index < len(content):
            if content[index : index + 2] == "\\|":
                cell.append("|")
                index += 2
                continue
            if content[index] == "|":
                cells.append("".join(cell).strip())
                cell.clear()
            else:
                cell.append(content[index])
            index += 1
        cells.append("".join(cell).strip())
        return cells

    def _inline(self, text: str, document: ReportDocument) -> str:
        parts: list[str] = []
        cursor = 0
        matches = sorted(
            [*(_IMAGE.finditer(text)), *(_LINK.finditer(text))], key=lambda match: match.start()
        )
        for match in matches:
            if match.start() < cursor:
                continue
            parts.append(html.escape(text[cursor : match.start()], quote=True))
            if match.re is _IMAGE:
                label = match.group("label")
                angle_target = match.group("angle_target")
                target = (
                    angle_target if angle_target is not None else match.group("plain_target")
                )
                reference = safe_chart_reference(target, document=document)
                if reference is None:
                    parts.append(html.escape(f"[\u56fe\u7247\u4e0d\u53ef\u7528: {label}]", quote=True))
                else:
                    parts.append(
                        f'<img src="{html.escape(reference, quote=True)}" '
                        f'alt="{html.escape(label, quote=True)}">'
                    )
            else:
                label, target = match.groups()
                safe_target = safe_link_target(target)
                if safe_target is None:
                    parts.append(html.escape(label, quote=True))
                else:
                    parts.append(
                        f'<a href="{html.escape(safe_target, quote=True)}">'
                        f"{html.escape(label, quote=True)}</a>"
                    )
            cursor = match.end()
        parts.append(html.escape(text[cursor:], quote=True))
        return "".join(parts)
