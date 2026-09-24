# M11 报告生成服务实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将报告生成从 `DataAnalysisAgent` 中抽离为可替换的 ReportService，统一输出 Markdown、HTML、DOCX，并保证结构化证据、图片边界和格式失败隔离。

**Architecture:** `ReportDocument` 携带当前任务的模型叙述、已验证指标、图表和模板版本；`ReportService` 先生成 canonical Markdown，再把它交给 HTML 和 DOCX renderer，并独立登记每种成功产物。Agent 继续负责模型调用和兼容字段映射，但不再直接写报告文件或调用 Word 函数。

**Tech Stack:** Python 3.10+、Pydantic 2、python-docx、现有 `EvidenceRegistry`、`ArtifactStorageService`、pytest；不新增 PDF、网络或 Markdown 第三方运行时依赖。

## Global Constraints

- PDF 只保留未来 renderer 扩展点，M11 不实现 PDF 转换。
- 报告中的事实数字只能来自验证状态为 `VERIFIED` 的 `MetricArtifact`。
- 图片只能来自当前任务中已登记、已通过路径边界检查且实际存在的 `ChartArtifact`。
- HTML 不执行模型提供的原始 HTML、脚本、危险链接或本地文件引用。
- DOCX 失败必须保留 Markdown；任何单一格式失败都不能删除分析结果或其他成功产物。
- 保留 `quick_analysis` 参数和现有兼容返回字段；保留 `utils/word_report_generator.py` 兼容导出。
- 所有测试使用 Fake LLM、Fake Renderer 和本地临时目录，不调用真实模型、网络、PDF 工具或生产对象存储。
- 每个任务按 RED → GREEN → 回归 → 独立提交执行。

## 文件地图

### 新增

- `src/data_analysis_agent/reports/models.py`：报告输入、格式结果和 bundle 模型。
- `src/data_analysis_agent/reports/templates.py`：模板协议和 `analysis-report-v1` 默认模板。
- `src/data_analysis_agent/reports/sanitize.py`：Markdown、链接、数字和图片引用过滤。
- `src/data_analysis_agent/reports/html.py`：canonical Markdown 到安全 HTML 的 renderer。
- `src/data_analysis_agent/reports/service.py`：报告编排、原子写入、失败隔离和存储登记。
- `tests/reports/__init__.py`：报告测试包。
- `tests/reports/test_models.py`、`test_sanitize.py`、`test_html.py`、`test_service.py`、`test_service_failures.py`：报告服务单元测试。
- `tests/agent/test_report_service_integration.py`：Agent 和 ReportService 集成契约。

### 修改

- `src/data_analysis_agent/domain/enums.py`：为 `ReportFormat` 增加 `HTML`。
- `src/data_analysis_agent/reports/word.py`：保留旧函数，增加 `WordReportRenderer` 适配协议。
- `src/data_analysis_agent/reports/__init__.py`：统一导出报告模型、服务和 renderer。
- `src/data_analysis_agent/agent/core.py`：注入 ReportService，移除直接报告写入/Word 调用，映射兼容返回值。
- `README.md`：补充 M11 API、输出和失败隔离说明。
- `task_plan.md`、`progress.md`：记录 M11 阶段和验证结果。

---

### Task 1: 报告格式与核心模型

**Files:**
- Modify: `src/data_analysis_agent/domain/enums.py`
- Create: `src/data_analysis_agent/reports/models.py`
- Modify: `src/data_analysis_agent/reports/__init__.py`
- Create: `tests/reports/__init__.py`
- Create: `tests/reports/test_models.py`

**Interfaces:**
- Consumes: `DomainModel`、`MetricArtifact`、`ChartArtifact`、`EvidenceValidation`、`ReportArtifact`。
- Produces: `ReportDocument`、`ReportFormatResult`、`ReportBundle`，供模板和 ReportService 使用。

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.domain.models import MetricArtifact
from data_analysis_agent.reports.models import ReportBundle, ReportDocument, ReportFormatResult


def test_html_is_a_supported_report_format():
    assert ReportFormat.HTML.value == "HTML"


def test_report_document_rejects_cross_task_artifacts(tmp_path: Path):
    task_id = uuid4()
    with pytest.raises(ValidationError, match="task"):
        ReportDocument(
            task_id=task_id,
            output_root=str(tmp_path),
            metric_artifacts=(MetricArtifact(task_id=uuid4(), name="revenue", value=1.0),),
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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_models.py
```

Expected: collection or assertion failure because `ReportFormat.HTML` and the three report models do not exist yet.

- [ ] **Step 3: Implement the minimal models**

Add `HTML = "HTML"` to `ReportFormat`. In `reports/models.py`, define strict `DomainModel` classes with these fields:

```python
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
    artifact: ReportArtifact | None = None
    error: StrictStr | None = None


class ReportBundle(DomainModel):
    task_id: UUID
    template_version: StrictStr
    markdown_content: StrictStr
    results: tuple[ReportFormatResult, ...]
    evidence_validation: EvidenceValidation
    storage_errors: tuple[StrictStr, ...] = ()
```

Export all three models from `data_analysis_agent.reports` and keep `extra="forbid"` through `DomainModel`.

- [ ] **Step 4: Run focused tests and the domain regression**

Run:

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_models.py tests/domain
```

Expected: all focused tests pass and existing domain tests remain green.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/domain/enums.py src/data_analysis_agent/reports/models.py src/data_analysis_agent/reports/__init__.py tests/reports
git commit -m "feat: add report service domain models"
```

### Task 2: Markdown、链接、数字和图片安全过滤

**Files:**
- Create: `src/data_analysis_agent/reports/sanitize.py`
- Create: `tests/reports/test_sanitize.py`

**Interfaces:**
- Consumes: `ReportDocument`、`EvidenceValidation`、`ChartArtifact`。
- Produces: `sanitize_markdown()`、`safe_link_target()`、`safe_chart_reference()`，供模板和 HTML renderer 使用。

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.models import ChartArtifact, MetricArtifact
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.sanitize import sanitize_markdown, safe_link_target


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


def test_sanitizer_only_keeps_verified_current_task_chart(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        chart_artifacts=(ChartArtifact(
            task_id=task_id,
            filename="trend.png",
            file_path=str(chart),
            verification_status=EvidenceVerificationStatus.VERIFIED,
        ),),
    )
    cleaned = sanitize_markdown(
        "![合法](trend.png) ![越界](../outside.png) ![外部](https://x.test/a.png)",
        document=document,
    )
    assert "trend.png" in cleaned
    assert "https://x.test/a.png" not in cleaned
    assert "图片不可用" in cleaned


def test_unsupported_numeric_tokens_are_marked_for_confirmation(tmp_path: Path):
    document = ReportDocument(
        task_id=uuid4(),
        output_root=str(tmp_path),
        metric_artifacts=(MetricArtifact(name="revenue", value=10.0, task_id=None),),
    )
    cleaned = sanitize_markdown("收入是999，增长率为12.5%", document=document, unsupported_numbers=("999", "12.5%"))
    assert "待确认" in cleaned
    assert "999" not in cleaned
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_sanitize.py
```

Expected: import failure because `sanitize.py` is not present.

- [ ] **Step 3: Implement the deterministic sanitizer**

Implement these exact behaviors:

```python
def safe_link_target(target: str) -> str | None:
    parsed = urlparse(target.strip())
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return target.strip()
    if parsed.scheme or target.strip().startswith(("/", "\\\\")):
        return None
    return None


def sanitize_markdown(
    markdown: str,
    *,
    document: ReportDocument,
    unsupported_numbers: Sequence[str] = (),
) -> str:
    sanitized_lines: list[str] = []
    in_code_block = False
    for raw_line in markdown.replace("\\r\\n", "\\n").split("\\n"):
        if raw_line.strip().startswith("```"):
            in_code_block = not in_code_block
            sanitized_lines.append(raw_line)
            continue
        if in_code_block:
            sanitized_lines.append(raw_line)
            continue
        sanitized_lines.append(_sanitize_non_code_line(raw_line, document, unsupported_numbers))
    return "\\n".join(sanitized_lines)
```

The implementation must process fenced code blocks separately, escape or remove raw HTML tags outside code, sanitize Markdown links, replace unknown/external image references with `[图片不可用: 图片描述]`, and replace unsupported numeric tokens outside code/link/image spans with `【待确认数字】`. It must resolve chart paths with `Path.resolve(strict=False)` and `relative_to(Path(document.output_root).resolve())`; any `ValueError`, `OSError`, directory, missing file, or task mismatch becomes a placeholder.

- [ ] **Step 4: Run focused tests**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_sanitize.py tests/services/test_evidence_registry.py
```

Expected: all sanitizer and existing evidence tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/reports/sanitize.py tests/reports/test_sanitize.py
git commit -m "feat: sanitize report content and evidence references"
```

### Task 3: 版本化模板与 HTML renderer

**Files:**
- Create: `src/data_analysis_agent/reports/templates.py`
- Create: `src/data_analysis_agent/reports/html.py`
- Create: `tests/reports/test_html.py`

**Interfaces:**
- Consumes: `ReportDocument`、`sanitize_markdown()`。
- Produces: `AnalysisReportTemplate(version="analysis-report-v1")` 和 `HtmlReportRenderer`。

- [ ] **Step 1: Write the failing tests**

```python
from uuid import uuid4
from pathlib import Path

from data_analysis_agent.domain.enums import EvidenceVerificationStatus, ReportFormat
from data_analysis_agent.domain.models import ChartArtifact, MetricArtifact
from data_analysis_agent.reports.html import HtmlReportRenderer
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.templates import AnalysisReportTemplate


def test_default_template_injects_verified_metric_and_chart(tmp_path: Path):
    task_id = uuid4()
    chart = tmp_path / "trend.png"
    chart.write_bytes(b"png")
    document = ReportDocument(
        task_id=task_id,
        output_root=str(tmp_path),
        narrative_markdown="# 叙述\n\n正文",
        metric_artifacts=(MetricArtifact(
            task_id=task_id,
            name="revenue",
            value=10.0,
            source_dataset_ids=(uuid4(),),
            execution_id=uuid4(),
            code_hash="a" * 64,
            verification_status=EvidenceVerificationStatus.VERIFIED,
        ),),
        chart_artifacts=(ChartArtifact(
            task_id=task_id,
            filename="trend.png",
            file_path=str(chart),
            verification_status=EvidenceVerificationStatus.VERIFIED,
        ),),
    )
    markdown = AnalysisReportTemplate().render_markdown(document)
    assert "revenue" in markdown
    assert "10" in markdown
    assert "![" in markdown


def test_html_renderer_escapes_text_and_preserves_report_structure(tmp_path: Path):
    output = tmp_path / "report.html"
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 标题\n\n- 项目")
    HtmlReportRenderer().render(markdown=AnalysisReportTemplate().render_markdown(document), document=document, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "<h1>" in html
    assert "<li>项目</li>" in html
    assert "<script" not in html
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_html.py
```

Expected: import failure because the template and HTML renderer are not present.

- [ ] **Step 3: Implement the default template**

`AnalysisReportTemplate.version` must equal `analysis-report-v1`. `render_markdown()` must:

1. return the original sanitized narrative unchanged when the document has no metrics, charts, or validation warnings, preserving old report exact-text tests;
2. otherwise render `# <title>`, the narrative, a `## 关键指标` table with only verified metrics, a `## 图表` section containing service-generated image references, and a `## 证据状态` section when validation is invalid or has pending claims;
3. format finite values with `format(value, ".15g")`, keep units and source columns, and never use model-provided replacements for metric values;
4. emit deterministic UTF-8 Markdown with one blank line between blocks.

- [ ] **Step 4: Implement the HTML renderer**

`HtmlReportRenderer.format` must be `ReportFormat.HTML`. Parse only the canonical subset: headings 1–3, paragraphs, unordered/ordered lists, pipe tables, fenced code blocks, safe links, and service-generated images. Use `html.escape()` for text and attributes, emit a complete UTF-8 document with a fixed `<meta charset="utf-8">`, and reject any raw HTML that survived input filtering. A missing image must emit the same visible `[图片不可用: 图片描述]` text used by the sanitizer.

- [ ] **Step 5: Run focused tests and Word regression**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_html.py tests/test_word_report_generator.py tests/contract/test_report_contract.py
```

Expected: new HTML/template tests and existing Word/report contract tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/data_analysis_agent/reports/templates.py src/data_analysis_agent/reports/html.py tests/reports/test_html.py
git commit -m "feat: add versioned report template and html renderer"
```

### Task 4: ReportService 编排、原子写入和格式失败隔离

**Files:**
- Create: `src/data_analysis_agent/reports/service.py`
- Modify: `src/data_analysis_agent/reports/__init__.py`
- Create: `tests/reports/test_service.py`
- Create: `tests/reports/test_service_failures.py`

**Interfaces:**
- Consumes: `ReportDocument`、`ReportTemplate`、`ReportRenderer`、`ArtifactStorageService`。
- Produces: `ReportService.generate(document, formats) -> ReportBundle`。

- [ ] **Step 1: Write failing service tests**

```python
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.service import ReportService


def test_service_generates_markdown_and_html_from_one_canonical_content(tmp_path: Path):
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    bundle = ReportService().generate(document, formats={ReportFormat.MARKDOWN, ReportFormat.HTML})
    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is True
    assert results[ReportFormat.HTML].generated is True
    assert bundle.markdown_content == "# 报告"
    assert "报告" in Path(results[ReportFormat.HTML].file_path).read_text(encoding="utf-8")


def test_docx_failure_keeps_markdown_and_returns_format_error(tmp_path: Path):
    class FailingDocxRenderer:
        format = ReportFormat.DOCX
        def render(self, *, markdown, document, output_path):
            raise RuntimeError("docx conversion failed")

    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    service = ReportService(renderers={ReportFormat.DOCX: FailingDocxRenderer()})
    bundle = service.generate(document, formats={ReportFormat.MARKDOWN, ReportFormat.DOCX})
    results = {item.format: item for item in bundle.results}
    assert results[ReportFormat.MARKDOWN].generated is True
    assert results[ReportFormat.DOCX].generated is False
    assert "docx conversion failed" in results[ReportFormat.DOCX].error
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_service.py tests/reports/test_service_failures.py
```

Expected: import failure because `ReportService` is not present.

- [ ] **Step 3: Implement `ReportService`**

Use this behavior:

```python
class ReportService:
    def __init__(self, *, template=None, renderers=None, artifact_storage=None, storage=None, evidence_registry=None):
        self.template = template or AnalysisReportTemplate()
        self.renderers = renderers or default_renderers()
        self.artifact_storage = artifact_storage
        self.storage = storage
        self.evidence_registry = evidence_registry

    def generate(self, document: ReportDocument, *, formats: Collection[ReportFormat]) -> ReportBundle:
        root = self._validate_output_root(document.output_root)
        validation = document.evidence_validation
        if self.evidence_registry is not None:
            validation = self.evidence_registry.validate_report(
                document.narrative_markdown,
                document.task_id,
            )
        validation = validation or EvidenceValidation(task_id=document.task_id, valid=True)
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
        for report_format in sorted(set(formats), key=lambda item: item.value):
            target = root / self._filename_for(report_format)
            temporary = root / f".{target.name}.{uuid4().hex}.tmp"
            try:
                if report_format is ReportFormat.MARKDOWN:
                    temporary.write_text(markdown, encoding="utf-8")
                else:
                    renderer = self.renderers[report_format]
                    renderer.render(markdown=markdown, document=document, output_path=temporary)
                os.replace(temporary, target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                results.append(self._failed_result(report_format, exc))
                continue
            results.append(self._store_success(report_format, target, document, storage_errors))
        return ReportBundle(
            task_id=document.task_id,
            template_version=document.template_version,
            markdown_content=markdown,
            results=tuple(results),
            evidence_validation=validation,
            storage_errors=tuple(storage_errors),
        )
```

Validate task IDs and output root first. Generate canonical Markdown once. For every requested format, use a unique temporary filename inside `output_root`, call the renderer, `os.replace()` the temporary file to the deterministic name (`最终分析报告.md`, `.html`, `.docx`), and produce a `ReportFormatResult`. Catch `OSError`, `RuntimeError`, `ValueError` and renderer exceptions per format, sanitize the error, remove only that format's temporary file, and continue.

The Markdown format uses a small file writer renderer. The HTML renderer is always available. The DOCX renderer is available through `WordReportRenderer`. If a requested format has no renderer, return `generated=False` with `unsupported report format` and continue. After a file succeeds, call `ArtifactStorageService.store_report()` with `MARKDOWN`, `HTML`, or `DOCX` and append the returned `ReportArtifact`; storage failures populate `storage_errors` without removing the local file.

- [ ] **Step 4: Add fake storage and failure-isolation tests**

Cover HTML renderer failure, DOCX renderer failure, Markdown write failure, storage registration failure, invalid output root, and cleanup of temporary files. Assert that each unaffected format remains generated and `ReportBundle` is returned without raising a format exception.

- [ ] **Step 5: Run focused tests**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports tests/storage/test_artifacts.py tests/storage/test_local.py
```

Expected: all report service and storage regressions pass.

- [ ] **Step 6: Commit**

```powershell
git add src/data_analysis_agent/reports/service.py src/data_analysis_agent/reports/__init__.py tests/reports
git commit -m "feat: add isolated report generation service"
```

### Task 5: Word renderer adapter and public exports

**Files:**
- Modify: `src/data_analysis_agent/reports/word.py`
- Modify: `src/data_analysis_agent/reports/__init__.py`
- Modify: `tests/test_word_report_generator.py`
- Create: `tests/reports/test_word_renderer.py`

**Interfaces:**
- Consumes: `ReportDocument` and canonical Markdown.
- Produces: `WordReportRenderer` implementing `ReportRenderer`; old `generate_word_report()` signature remains unchanged.

- [ ] **Step 1: Write failing adapter test**

```python
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.reports.models import ReportDocument
from data_analysis_agent.reports.word import WordReportRenderer


def test_word_renderer_implements_report_renderer(tmp_path: Path):
    output = tmp_path / "report.docx"
    document = ReportDocument(task_id=uuid4(), output_root=str(tmp_path), narrative_markdown="# 报告")
    renderer = WordReportRenderer()
    assert renderer.format is ReportFormat.DOCX
    renderer.render(markdown="# 报告", document=document, output_path=output)
    assert output.exists()
```

- [ ] **Step 2: Run RED**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_word_renderer.py
```

Expected: import failure because `WordReportRenderer` is not defined.

- [ ] **Step 3: Add the adapter without changing the compatibility function**

Implement `WordReportRenderer.format = ReportFormat.DOCX`. Its `render()` converts `document.chart_artifacts` to the existing figure mapping (`filename`, `file_path`, `title`/`description`) and calls the existing `generate_word_report()` with `session_output_dir=document.output_root`. Keep `utils/word_report_generator.py` and the old function importable with the original parameters and return value.

- [ ] **Step 4: Run Word regressions**

```powershell
E:\anaconda\python.exe -m pytest -q tests/reports/test_word_renderer.py tests/test_word_report_generator.py tests/test_word_report_integration.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/reports/word.py src/data_analysis_agent/reports/__init__.py tests/reports/test_word_renderer.py tests/test_word_report_generator.py
git commit -m "feat: adapt word renderer to report service"
```

### Task 6: 接入 DataAnalysisAgent 并保持兼容结果

**Files:**
- Modify: `src/data_analysis_agent/agent/core.py`
- Create: `tests/agent/test_report_service_integration.py`
- Modify: `tests/contract/test_report_contract.py`
- Modify: `tests/agent/test_evidence_integration.py` only if the new HTML/evidence fields need assertions.

**Interfaces:**
- Consumes: `ReportService`, `ReportDocument`, `ReportBundle`, `ReportFormat`。
- Produces: existing result keys plus `html_report_file_path`, `html_report_generated`, `html_report_error`, `html_report_download_url`, `report_results`, and `report_artifacts`.

- [ ] **Step 1: Write failing Agent integration tests**

```python
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from data_analysis_agent.agent.core import DataAnalysisAgent
from data_analysis_agent.domain.enums import ReportFormat
from data_analysis_agent.reports.html import HtmlReportRenderer
from data_analysis_agent.reports.service import ReportService
from data_analysis_agent.services.evidence import EvidenceRegistry
from tests.fixtures.fake_llm import FakeLLM, yaml_response


def make_compatibility_agent(tmp_path, *, generate_word_report):
    task_id = uuid4()
    agent = object.__new__(DataAnalysisAgent)
    agent.session_output_dir = str(tmp_path)
    agent.base_output_dir = str(tmp_path)
    agent.analysis_results = []
    agent.current_round = 1
    agent.conversation_history = []
    agent.generate_word_report = generate_word_report
    agent.config = SimpleNamespace(api_key="", base_url="", max_tokens=128)
    agent.llm = FakeLLM([yaml_response("analysis_complete", final_report="# 报告")])
    agent.llm_port = SimpleNamespace(request_report=lambda **kwargs: "# 报告")
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent.artifact_records = []
    agent.storage = None
    agent.artifact_storage = None
    return agent


def test_agent_delegates_formats_to_report_service_and_keeps_legacy_fields(tmp_path):
    calls = []

    class FakeReportService:
        def generate(self, document, *, formats):
            calls.append((document, set(formats)))
            return SimpleNamespace(
                markdown_content="# 报告",
                results=(),
                storage_errors=(),
                evidence_validation=SimpleNamespace(model_dump=lambda mode="json": {"valid": True}),
            )

    agent = make_compatibility_agent(tmp_path, generate_word_report=False)
    agent.report_service = FakeReportService()

    result = agent._generate_final_report()

    assert calls
    assert ReportFormat.MARKDOWN in calls[0][1]
    assert ReportFormat.HTML in calls[0][1]
    assert result["final_report"] == "# 报告"


def test_word_failure_preserves_markdown_and_exposes_legacy_error(tmp_path):
    class FailingDocxRenderer:
        format = ReportFormat.DOCX
        def render(self, *, markdown, document, output_path):
            raise RuntimeError("baseline Word failure")

    agent = make_compatibility_agent(tmp_path, generate_word_report=True)
    agent.report_service = ReportService(
        renderers={
            ReportFormat.DOCX: FailingDocxRenderer(),
            ReportFormat.HTML: HtmlReportRenderer(),
        }
    )
    result = agent._generate_final_report()
    assert Path(result["report_file_path"]).exists()
    assert result["word_report_generated"] is False
    assert "baseline Word failure" in result["word_report_error"]
```

- [ ] **Step 2: Run RED**

```powershell
E:\anaconda\python.exe -m pytest -q tests/agent/test_report_service_integration.py tests/contract/test_report_contract.py
```

Expected: the new integration test fails because `DataAnalysisAgent` has no report service delegation or HTML result mapping.

- [ ] **Step 3: Add lazy-compatible ReportService injection**

Add an optional `report_service` keyword-only-at-end constructor parameter without changing existing positional parameters. Store it after `evidence_registry` initialization. Add `_get_report_service()` that returns the injected service, or lazily creates one using the Agent's `artifact_storage`, `storage`, and evidence registry; this supports old `object.__new__(DataAnalysisAgent)` fixtures.

- [ ] **Step 4: Replace direct report file and Word logic**

Keep the existing LLM request, fallback text, logging, and structured exception propagation. After the model response, build `ReportDocument` from the Agent task ID, session output directory, narrative Markdown, and the evidence registry snapshot. Request `{ReportFormat.MARKDOWN, ReportFormat.HTML}` plus `ReportFormat.DOCX` when `generate_word_report` is true. Call `_get_report_service().generate()` once.

Map the bundle as follows:

- `final_report` and `report_file_path` come from `markdown_content` and the Markdown result;
- `html_report_file_path`, `html_report_generated`, and `html_report_error` come from the HTML result;
- `report_download_url` and `html_report_download_url` come from the corresponding result `download_url` values;
- `word_report_file_path`, `word_report_generated`, and `word_report_error` come from the DOCX result; when DOCX was requested but failed before a file exists, retain the deterministic `session_output_dir/最终分析报告.docx` path to preserve the current compatibility contract;
- `word_report_download_url` comes from the DOCX result `download_url`;
- append successful report Artifact records to `artifact_records` and use the existing storage URL fields;
- retain metric/chart/evidence snapshots and `storage_error` in the returned dictionary.

Remove the direct `Path(report_file_path).write_text(final_report_content, encoding="utf-8")`, direct invocation of `generate_word_report`, and duplicate report storage calls from `_generate_final_report()`. Leave `_store_figure_artifacts()` intact for M10 chart artifact persistence; the ReportService consumes the already registered chart metadata.

- [ ] **Step 5: Run focused Agent and compatibility tests**

```powershell
E:\anaconda\python.exe -m pytest -q tests/agent tests/contract/test_report_contract.py tests/integration/test_analysis_flow.py tests/llm/test_agent_structured_boundary.py
```

Expected: new integration tests and all existing Agent/report compatibility tests pass, including Word failure preserving Markdown and old `object.__new__` fixtures.

- [ ] **Step 6: Commit**

```powershell
git add src/data_analysis_agent/agent/core.py tests/agent/test_report_service_integration.py tests/contract/test_report_contract.py tests/agent/test_evidence_integration.py
git commit -m "feat: route agent reports through report service"
```

### Task 7: 文档、计划更新和全量验证

**Files:**
- Modify: `README.md`
- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `docs/superpowers/specs/2026-09-24-report-generation-service-design.md` only if implementation proves an approved design detail needs a precise correction.

**Interfaces:**
- Consumes: all completed ReportService tasks and Agent compatibility results.
- Produces: documented public import path and verified M11 acceptance evidence.

- [ ] **Step 1: Add documentation assertions or examples**

Document the public imports and a minimal offline example:

```python
from data_analysis_agent.reports import ReportDocument, ReportFormat, ReportService

document = ReportDocument(
    task_id=task_id,
    output_root=session_dir,
    narrative_markdown="# 分析报告",
)
bundle = ReportService().generate(
    document,
    formats={ReportFormat.MARKDOWN, ReportFormat.HTML},
)
```

Document that PDF is not part of M11, that DOCX errors leave Markdown/HTML intact, and that only task-scoped verified charts and metrics enter outputs.

- [ ] **Step 2: Update persistent planning files**

Mark every M11 task complete only after its tests pass. Add the focused and full test counts, any warnings, exact failure causes and fixes to `progress.md`; put unresolved future PDF work in the next-stage notes rather than marking it complete.

- [ ] **Step 3: Run the complete verification suite**

```powershell
E:\anaconda\python.exe -m pytest -q
E:\anaconda\python.exe -m compileall -q src
git diff --check
```

Expected: all existing and new tests pass; compileall exits 0; diff check emits no whitespace errors. The final test count must be recorded from the actual command output.

- [ ] **Step 4: Inspect the final diff and status**

```powershell
git status --short
git log --oneline --decorate -10
git diff HEAD~7..HEAD --stat
```

Confirm no API keys, model responses, host secrets, temporary report files, or untracked test artifacts are committed.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md task_plan.md progress.md
git commit -m "docs: document report generation service"
```

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-24-report-generation-service.md`. Execute the tasks in order, with a RED/GREEN checkpoint and commit after each task. Use the approved design at `docs/superpowers/specs/2026-09-24-report-generation-service-design.md` as the source of truth.
