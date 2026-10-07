from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import EvidenceVerificationStatus, ReportFormat
from data_analysis_agent.domain.models import (
    EvidenceValidation,
    MetricArtifact,
    ReportArtifact,
)
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.service import ReportService


class RecordingRenderer:
    def __init__(self, report_format: ReportFormat) -> None:
        self.format = report_format
        self.markdown: list[str] = []

    def render(self, *, markdown: str, document: ReportDocument, output_path: Path) -> str:
        self.markdown.append(markdown)
        Path(output_path).write_text(f"{self.format.value}: {markdown}", encoding="utf-8")
        return str(output_path)


def test_service_generates_markdown_and_html_from_one_canonical_content(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    renderer = RecordingRenderer(ReportFormat.HTML)

    bundle = ReportService(
        allowed_output_root=tmp_path,
        renderers={ReportFormat.HTML: renderer},
    ).generate(
        document, formats={ReportFormat.MARKDOWN, ReportFormat.HTML}
    )

    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is True
    assert results[ReportFormat.HTML].generated is True
    assert bundle.markdown_content == "# 报告"
    assert renderer.markdown == [bundle.markdown_content]
    assert "报告" in Path(results[ReportFormat.HTML].file_path).read_text(encoding="utf-8")


def test_service_registers_successful_file_with_artifact_storage(tmp_path: Path):
    class FakeArtifactStorage:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def store_report(self, **kwargs):
            self.calls.append(kwargs)
            return ReportArtifact(format=ReportFormat.MARKDOWN, file_path="memory://report.md")

    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    storage = FakeArtifactStorage()

    bundle = ReportService(
        allowed_output_root=tmp_path,
        artifact_storage=storage,
    ).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    result = bundle.results[0]
    assert result.artifact is not None
    assert storage.calls[0]["format"] is ReportFormat.MARKDOWN
    assert storage.calls[0]["mime_type"] == "text/markdown; charset=utf-8"


def test_service_uses_evidence_registry_before_canonical_rendering(tmp_path: Path):
    class EvidenceRegistry:
        def validate_report(self, narrative: str, task_id):
            return EvidenceValidation(
                task_id=task_id,
                valid=False,
                unsupported_numeric_claims=("999",),
            )

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="结论是 999"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path,
        evidence_registry=EvidenceRegistry(),
    ).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert "999" not in bundle.markdown_content
    assert "待确认数字" not in bundle.markdown_content
    assert bundle.markdown_content == ""


def test_service_rejects_missing_output_root_before_rendering(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path / "does-not-exist"), narrative_markdown="# 报告"
    )

    with pytest.raises(ValueError, match="output root"):
        ReportService(allowed_output_root=tmp_path).generate(
            document, formats={ReportFormat.MARKDOWN}
        )


def test_service_requires_an_explicit_trusted_output_root(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    with pytest.raises(ValueError, match="trusted output root"):
        ReportService().generate(document, formats={ReportFormat.MARKDOWN})


def test_service_marks_numeric_narrative_pending_without_evidence(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        narrative_markdown="Revenue is 999.",
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is False
    assert bundle.evidence_validation.unsupported_numeric_claims == ("999",)
    assert "999" not in bundle.markdown_content
    assert "待确认数字" not in bundle.markdown_content
    assert "UNSUPPORTED_NUMERIC_CLAIM" not in bundle.markdown_content
    assert bundle.markdown_content == ""


def test_service_omits_unsupported_numeric_sentence_without_placeholder(
    tmp_path: Path,
):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown=(
            "补贴规模约为商家实收的1.5倍，成本结构失衡。\n\n"
            "第二段只保留定性结论。"
        ),
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is False
    assert bundle.evidence_validation.unsupported_numeric_claims == ("1.5",)
    assert "1.5" not in bundle.markdown_content
    assert "待确认数字" not in bundle.markdown_content
    assert "UNSUPPORTED_NUMERIC_CLAIM" not in bundle.markdown_content
    assert "第二段只保留定性结论。" in bundle.markdown_content


def test_service_preserves_numeric_fact_when_verified_metric_matches(tmp_path: Path):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="Revenue is 999.",
        metric_artifacts=(
            MetricArtifact(
                task_id=task_id,
                name="revenue",
                value=999.0,
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is True
    assert bundle.evidence_validation.unsupported_numeric_claims == ()
    assert "Revenue is 999." in bundle.markdown_content


def test_service_preserves_verified_execution_output_numbers(tmp_path: Path):
    task_id = uuid4()
    dataset_id = uuid4()
    execution_id = uuid4()
    metrics = tuple(
        MetricArtifact(
            task_id=task_id,
            name=f"execution_output_{index}",
            value=value,
            unit=unit,
            formula="numeric value emitted by successful dataset analysis execution",
            source_dataset_ids=(dataset_id,),
            execution_id=execution_id,
            code_hash="a" * 64,
            verification_status=EvidenceVerificationStatus.VERIFIED,
            metadata={"evidence_source": "successful_execution_output"},
        )
        for index, (value, unit) in enumerate(
            ((2019.0, None), (2_000_000.0, None), (66.6, "%"))
        )
    )
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="统计年份为2019，GMV为2,000,000元，转化率为66.6%。",
        metric_artifacts=metrics,
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is True
    assert bundle.evidence_validation.unsupported_numeric_claims == ()
    assert "待确认数字" not in bundle.markdown_content
    assert "2,000,000" in bundle.markdown_content


def test_service_marks_unsupported_percentage_pending(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        narrative_markdown="Conversion increased by 12.5%.",
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is False
    assert bundle.evidence_validation.unsupported_numeric_claims == ("12.5%",)
    assert "12.5%" not in bundle.markdown_content


def test_service_does_not_convert_ratio_metrics_into_unstated_percentages(
    tmp_path: Path,
):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="Conversion increased by 12.5%.",
        metric_artifacts=(
            MetricArtifact(
                task_id=task_id,
                name="conversion_ratio",
                value=0.125,
                verification_status=EvidenceVerificationStatus.VERIFIED,
            ),
        ),
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is False
    assert bundle.evidence_validation.unsupported_numeric_claims == ("12.5%",)


def test_service_honors_supplied_evidence_validation(tmp_path: Path):
    task_id = uuid4()
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="Revenue is 999.",
        evidence_validation=EvidenceValidation(task_id=task_id, valid=True),
    )

    bundle = ReportService(allowed_output_root=tmp_path).generate(
        document, formats={ReportFormat.MARKDOWN}
    )

    assert bundle.evidence_validation.valid is True
    assert "Revenue is 999." in bundle.markdown_content


def test_service_keeps_storage_dependencies_separate_and_returns_download_url(
    tmp_path: Path,
):
    class FakeArtifactStorage:
        def store_report(self, **kwargs):
            return ReportArtifact(
                format=ReportFormat.MARKDOWN,
                file_path="memory://report.md",
            )

    class FakeStorage:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
            self.calls.append(uri)
            return f"download://{uri.removeprefix('memory://')}"

    storage = FakeStorage()
    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path,
        artifact_storage=FakeArtifactStorage(),
        storage=storage,
    ).generate(document, formats={ReportFormat.MARKDOWN})

    assert bundle.results[0].download_url == "download://report.md"
    assert bundle.results[0].content_url == "download://report.md"
    assert storage.calls == ["memory://report.md"]


def test_service_maps_local_download_token_to_protected_content_url(
    tmp_path: Path,
):
    artifact_id = uuid4()

    class FakeArtifactStorage:
        def store_report(self, **kwargs):
            return ReportArtifact(
                artifact_id=artifact_id,
                format=ReportFormat.MARKDOWN,
                file_path="local://tasks/report.md",
            )

    class FakeStorage:
        def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
            assert uri == "local://tasks/report.md"
            return "local-download://opaque-token"

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path,
        artifact_storage=FakeArtifactStorage(),
        storage=FakeStorage(),
    ).generate(document, formats={ReportFormat.MARKDOWN})

    result = bundle.results[0]
    assert result.download_url == "local-download://opaque-token"
    assert result.content_url == (
        f"/api/artifacts/{artifact_id}/content?"
        "download_url=local-download%3A%2F%2Fopaque-token"
    )


def test_service_does_not_treat_storage_as_artifact_registry(tmp_path: Path):
    class StorageOnly:
        def store_report(self, **kwargs):
            raise AssertionError("storage was incorrectly used as artifact storage")

        def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
            raise AssertionError("no artifact should mean no download URL request")

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path, storage=StorageOnly()
    ).generate(document, formats={ReportFormat.MARKDOWN})

    assert bundle.results[0].generated is True
    assert bundle.results[0].artifact is None
    assert bundle.results[0].download_url is None


def test_service_does_not_treat_storage_as_artifact_storage(tmp_path: Path):
    class StorageOnly:
        def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
            return f"download://{uri}"

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    bundle = ReportService(
        allowed_output_root=tmp_path, storage=StorageOnly()
    ).generate(document, formats={ReportFormat.MARKDOWN})

    assert bundle.results[0].generated is True
    assert bundle.results[0].artifact is None
    assert bundle.storage_errors == ()


def test_service_rejects_output_root_outside_trusted_base(tmp_path: Path):
    trusted = tmp_path / "tasks"
    external = tmp_path / "external"
    trusted.mkdir()
    external.mkdir()
    document = ReportDocument(
        task_id=uuid4(), output_root=str(external), narrative_markdown="# 报告"
    )

    with pytest.raises(ValueError, match="trusted output root"):
        ReportService(allowed_output_root=trusted).generate(
            document, formats={ReportFormat.MARKDOWN}
        )


def test_service_bundle_uses_validated_template_version(tmp_path: Path):
    class CustomTemplate:
        version = "custom-v2"

        def render_markdown(self, document):
            return document.narrative_markdown

    document = ReportDocument(
        task_id=uuid4(),
        template_version="custom-v2",
        output_root=str(tmp_path),
        narrative_markdown="# 报告",
    )

    bundle = ReportService(
        allowed_output_root=tmp_path, template=CustomTemplate()
    ).generate(document, formats={ReportFormat.MARKDOWN})

    assert bundle.template_version == "custom-v2"


def test_service_rejects_template_version_mismatch(tmp_path: Path):
    class CustomTemplate:
        version = "custom-v2"

        def render_markdown(self, document):
            return document.narrative_markdown

    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        narrative_markdown="# 报告",
    )

    with pytest.raises(ValueError, match="template version"):
        ReportService(
            allowed_output_root=tmp_path, template=CustomTemplate()
        ).generate(document, formats={ReportFormat.MARKDOWN})


def test_service_rejects_template_without_validated_version(tmp_path: Path):
    class UnversionedTemplate:
        def render_markdown(self, document):
            return document.narrative_markdown

    document = ReportDocument(
        task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告"
    )

    with pytest.raises(ValueError, match="template version"):
        ReportService(
            allowed_output_root=tmp_path, template=UnversionedTemplate()
        ).generate(document, formats={ReportFormat.MARKDOWN})
