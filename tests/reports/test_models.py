from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import MetricArtifact
from data_analysis_agent.reports import ReportBundle, ReportDocument, ReportFormatResult


def test_html_is_a_supported_report_format():
    assert ReportFormat.HTML.value == "HTML"


def test_report_document_rejects_cross_task_artifacts(tmp_path: Path):
    task_id = uuid4()

    with pytest.raises(ValidationError, match="task"):
        ReportDocument(
            task_id=task_id,
            output_root=str(tmp_path),
            metric_artifacts=(
                MetricArtifact(task_id=uuid4(), name="revenue", value=1.0),
            ),
        )


def test_report_bundle_is_json_serializable(tmp_path: Path):
    task_id = uuid4()
    bundle = ReportBundle(
        task_id=task_id,
        template_version="analysis-report-v1",
        markdown_content="# 报告",
        results=(ReportFormatResult(format=ReportFormat.MARKDOWN, generated=False),),
        evidence_validation={"task_id": task_id, "valid": True},
    )

    payload = bundle.model_dump(mode="json")

    assert payload["task_id"] == str(task_id)
    assert payload["results"][0]["format"] == "MARKDOWN"


def test_report_models_are_publicly_exported():
    assert ReportDocument.__module__ == "data_analysis_agent.reports.models"
    assert ReportFormatResult.__module__ == "data_analysis_agent.reports.models"
    assert ReportBundle.__module__ == "data_analysis_agent.reports.models"
