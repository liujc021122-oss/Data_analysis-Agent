from pathlib import Path
from uuid import uuid4

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import ChartArtifact
from data_analysis_agent.reports import WordReportRenderer as ExportedWordReportRenderer
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.service import default_renderers
from data_analysis_agent.reports.word import WordReportRenderer


def test_word_renderer_implements_report_renderer(tmp_path: Path):
    output = tmp_path / "report.docx"
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    renderer = WordReportRenderer()

    assert renderer.format is ReportFormat.DOCX
    renderer.render(markdown="# 报告", document=document, output_path=output)
    assert output.exists()


def test_word_renderer_maps_charts_and_returns_legacy_result(tmp_path: Path, monkeypatch):
    task_id = uuid4()
    output = tmp_path / "report.docx"
    chart = ChartArtifact(
        task_id=task_id,
        filename="display-name.png",
        file_path=str(tmp_path / "actual-name.png"),
        title="趋势图",
        description="月度趋势",
    )
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="# 报告",
        chart_artifacts=(chart,),
    )
    calls = []

    def fake_generate_word_report(**kwargs):
        calls.append(kwargs)
        return "legacy-result"

    monkeypatch.setattr(
        "data_analysis_agent.reports.word.generate_word_report",
        fake_generate_word_report,
    )

    result = WordReportRenderer().render(
        markdown="# canonical", document=document, output_path=output
    )

    assert result == "legacy-result"
    assert calls == [
        {
            "markdown_content": "# canonical",
            "output_path": output,
            "session_output_dir": str(tmp_path),
            "figures": [
                {
                    "filename": "display-name.png",
                    "file_path": str(tmp_path / "actual-name.png"),
                    "title": "趋势图",
                    "description": "月度趋势",
                }
            ],
        }
    ]


def test_word_renderer_is_public_and_used_by_default():
    assert ExportedWordReportRenderer is WordReportRenderer
    assert isinstance(default_renderers()[ReportFormat.DOCX], WordReportRenderer)
