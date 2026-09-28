from uuid import UUID

from pydantic import StrictBool, StrictStr, model_validator

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import (
    ChartArtifact,
    DomainModel,
    EvidenceValidation,
    MetricArtifact,
    ReportArtifact,
)


class ReportDocument(DomainModel):
    task_id: UUID
    template_version: StrictStr = "analysis-report-v1"
    title: StrictStr = "数据分析报告"
    narrative_markdown: StrictStr = ""
    output_root: StrictStr
    metric_artifacts: tuple[MetricArtifact, ...] = ()
    chart_artifacts: tuple[ChartArtifact, ...] = ()
    evidence_validation: EvidenceValidation | None = None

    @model_validator(mode="after")
    def _same_task(self) -> "ReportDocument":
        for artifact in (*self.metric_artifacts, *self.chart_artifacts):
            if artifact.task_id is not None and artifact.task_id != self.task_id:
                raise ValueError("report artifact task does not match document task")
        return self


class ReportFormatResult(DomainModel):
    format: ReportFormat
    generated: StrictBool = False
    file_path: StrictStr | None = None
    download_url: StrictStr | None = None
    content_url: StrictStr | None = None
    artifact: ReportArtifact | None = None
    error: StrictStr | None = None


class ReportBundle(DomainModel):
    task_id: UUID
    template_version: StrictStr
    markdown_content: StrictStr
    results: tuple[ReportFormatResult, ...]
    evidence_validation: EvidenceValidation
    storage_errors: tuple[StrictStr, ...] = ()
