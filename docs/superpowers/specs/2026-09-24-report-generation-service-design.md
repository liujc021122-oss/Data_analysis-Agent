# M11 报告生成服务设计

**日期：** 2026-09-24
**状态：** 设计已确认，等待书面规范审阅
**范围：** Markdown、HTML、DOCX；PDF 仅保留扩展接口

## 目标

将报告生成从 `DataAnalysisAgent` 主流程中抽离为独立、可测试、可替换的报告服务。报告以结构化指标和图表为事实来源，模型只提供叙述草稿；Markdown、HTML 和 DOCX 使用同一份经过过滤的内容基准，单一格式失败不影响分析结果或其他产物。

## 范围与非目标

本次实现包含：

- 新增 `ReportDocument`、`ReportFormatResult` 和 `ReportBundle` 等报告服务模型；
- 新增可替换的报告模板接口和默认 `analysis-report-v1` 模板；
- 支持 Markdown、HTML、DOCX 输出；
- 将结构化指标、验证状态和当前任务图表统一注入报告；
- 对模型叙述执行 Markdown 安全过滤和证据校验；
- 复用 `ArtifactStorageService` 登记报告元数据和下载地址；
- 保留现有 Agent、`quick_analysis` 和 Word 兼容入口。

本次不包含：

- PDF 转换实现；
- 新的数据库表或迁移；
- LLM Provider、状态机和文件存储后端重构；
- 让前端直接获得宿主机文件路径；
- 通过模型输出重新计算或猜测指标。

## 现有代码约束

- `src/data_analysis_agent/reports/word.py` 已包含稳定的 Word Markdown 子集渲染器；`utils/word_report_generator.py` 是兼容导出层，应继续保留。
- `DataAnalysisAgent._generate_final_report()` 当前同时负责模型调用、Markdown 写文件、Word 转换、存储登记和兼容结果组装；M11 将这些产物职责交给 `ReportService`。
- M10 已提供 `MetricArtifact`、`ChartArtifact`、`EvidenceValidation` 和任务级 `EvidenceRegistry`。
- `ArtifactStorageService.store_report()` 已提供 provider-neutral 的报告文件和元数据登记能力。
- `ReportFormat` 当前包含 `MARKDOWN`、`DOCX`；本次增加 `HTML`，不增加 `PDF` 枚举值，避免在没有实现时产生可调用但不可用的格式。

## 设计概览

```text
模型调用
  └─ narrative_markdown（只作为叙述草稿）
       ↓
ReportDocument
  ├─ 已验证 MetricArtifact
  ├─ 当前任务 ChartArtifact
  ├─ EvidenceValidation
  └─ analysis-report-v1
       ↓
ReportService
  ├─ 安全过滤和证据校验
  ├─ 生成 canonical Markdown
  ├─ Markdown 文件
  ├─ HTML renderer
  ├─ DOCX renderer
  └─ ArtifactStorageService
```

报告服务不负责调用模型。Agent 可以继续调用现有 LLM Port 获取叙述草稿，但在获得草稿后只构造 `ReportDocument` 并调用 `ReportService`。这样报告渲染可以在没有 API Key、模型或网络的测试环境中独立验证。

## 核心模型和接口

### `ReportDocument`

`ReportDocument` 使用项目现有的严格领域模型约定，拒绝未知字段并支持 JSON 序列化：

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
```

服务在渲染前额外检查：

- 指标和图表的 `task_id` 必须与文档任务一致；
- 只有 `VERIFIED` 指标进入系统指标表；
- 只有当前任务、路径位于 `output_root` 内且文件存在的图表可以生成图片引用；
- `output_root` 必须是任务输出目录，不能是项目根目录或任意用户输入路径。

### `ReportFormatResult`

每个格式独立返回结果：

```python
class ReportFormatResult(DomainModel):
    format: ReportFormat
    generated: StrictBool = False
    file_path: StrictStr | None = None
    artifact: ReportArtifact | None = None
    error: StrictStr | None = None
```

`file_path` 仅作为本地兼容结果；API 或前端使用 `ReportArtifact` 的存储 URI / 下载 URL，不直接暴露宿主绝对路径。

### `ReportBundle`

```python
class ReportBundle(DomainModel):
    task_id: UUID
    template_version: StrictStr
    markdown_content: StrictStr
    results: tuple[ReportFormatResult, ...]
    evidence_validation: EvidenceValidation
    storage_errors: tuple[StrictStr, ...] = ()
```

`ReportBundle` 是 Agent 和 API 层之间的结构化报告结果。Agent 兼容层从它映射现有字段，同时新增 HTML 路径、格式结果和报告 Artifact 快照。

### `ReportTemplate` 和 renderer

```python
class ReportTemplate(Protocol):
    version: str

    def render_markdown(self, document: ReportDocument) -> str:
        ...


class ReportRenderer(Protocol):
    format: ReportFormat

    def render(
        self,
        *,
        markdown: str,
        document: ReportDocument,
        output_path: Path,
    ) -> None:
        ...
```

默认模板负责生成 canonical Markdown，包括标题、叙述、系统指标表、来源信息、验证提示和由服务生成的图表引用。HTML renderer 和现有 Word renderer 都消费这份 canonical Markdown，不各自重新组织事实内容。

`ReportService` 的公开调用约定为：

```python
class ReportService:
    def generate(
        self,
        document: ReportDocument,
        *,
        formats: Collection[ReportFormat],
    ) -> ReportBundle:
        ...
```

服务依赖通过构造函数注入：模板、renderer 映射、可选 `ArtifactStorageService`、可选 `EvidenceRegistry`。默认构造使用内置模板和 Markdown/HTML/DOCX renderer；测试可以注入 fake renderer 验证失败隔离。

## 内容和安全规则

### 结构化事实

- 系统指标表只使用 `EvidenceVerificationStatus.VERIFIED` 的 `MetricArtifact`。
- 指标显示值、单位、公式和来源列由模板注入；模型不能覆盖这些字段。
- `EvidenceRegistry.validate_report()` 继续检查叙述中的数字。无法对应结构化指标的数字被转换为明确的“待确认”标记，并保留在 `EvidenceValidation.unsupported_numeric_claims`，不能以已确认事实输出。
- 事实和解释 claim 继续使用 M10 的 `EvidenceClaim` 状态；没有证据的解释可以保留，但标记为 `PENDING_CONFIRMATION`。

### 图片

- 模型输出中的任意图片 URL、绝对路径、`../` 路径和未知文件名都不直接使用。
- 服务根据已验证 `ChartArtifact` 生成逻辑图片引用；HTML 使用任务报告目录内的相对引用，DOCX 使用已通过边界检查的本地文件。
- 图表文件缺失、是目录、越界或任务 ID 不匹配时，三种格式统一输出 `[图片不可用: 图片描述]` 占位，而不是错误链接。
- 图表显示名可以不同于真实文件名，但映射由 `ChartArtifact` 维护，不由模型路径决定。

### Markdown 和 HTML

安全过滤器只保留报告所需的 Markdown 子集：标题、段落、粗体/斜体、表格、无序/有序列表、代码块、安全链接和服务生成的图片。

- 原始 HTML 标签和事件属性被转义或移除；不会把模型文本当作 HTML 执行。
- 链接只允许 `http`、`https` 和 `mailto` 协议；`javascript`、`data`、`file`、UNC 和本地路径被拒绝。
- 代码块作为文本转义，不执行其中内容。
- HTML renderer 使用转义后的文本和白名单属性生成 HTML，不依赖浏览器端清理。

## 生成流程和失败隔离

`ReportService.generate()` 按以下步骤执行：

1. 校验任务 ID、模板版本、证据和输出目录边界；
2. 对模型叙述执行 Markdown 过滤并得到 evidence validation；
3. 由模板生成 canonical Markdown；
4. 以临时文件写入 Markdown，成功后原子替换为 `最终分析报告.md`；
5. 独立调用 HTML renderer，生成 `最终分析报告.html`；
6. 若请求 DOCX，独立调用现有 Word renderer，生成 `最终分析报告.docx`；
7. 对每个成功文件调用 `ArtifactStorageService.store_report()`；
8. 汇总每个格式的成功、路径、Artifact 和错误，返回 `ReportBundle`。

每个格式都使用独立的临时文件和异常边界。HTML 或 DOCX 失败时，已成功的 Markdown 和其他格式继续保留；存储登记失败只记录 `storage_errors`，不删除本地产物。格式错误消息经过现有异常脱敏函数处理，不包含 API Key、宿主敏感路径或原始模型响应。

模型叙述调用失败时，Agent 继续沿用现有兼容契约生成安全的 Markdown fallback，再交给报告服务渲染；结构化 LLM 异常仍由兼容层按现有测试约定传播，报告文件不会因为异常传播而被删除。

## Agent 集成和兼容性

- `DataAnalysisAgent` 增加可选的内部 `report_service` 注入点，默认创建 `ReportService`；不改变已有公共位置参数和 `quick_analysis` 签名。
- `_generate_final_report()` 保留 LLM 请求和日志，但删除直接写 Markdown、直接调用 `generate_word_report()` 和重复的存储登记逻辑，改为构造 `ReportDocument` 并消费 `ReportBundle`。
- 继续返回 `final_report`、`report_file_path`、`word_report_file_path`、`word_report_generated`、`word_report_error`、`report_download_url` 和 `word_report_download_url`。
- 新增 `html_report_file_path`、`html_report_generated`、`html_report_error`、`report_results` 和 `report_artifacts`，不改变旧字段含义。
- `utils/word_report_generator.py` 和 `data_analysis_agent.reports.word.generate_word_report()` 保留为兼容入口；新实现统一从 `data_analysis_agent.reports` 导入。
- Agent 仍将分析结果、指标 Artifact、图表 Artifact 和 evidence validation 作为独立字段返回；Word 失败不会清除这些字段。

## 文件边界

```text
src/data_analysis_agent/reports/
├── __init__.py              # 统一公开 ReportService 和报告模型
├── models.py                # ReportDocument、ReportFormatResult、ReportBundle
├── templates.py             # ReportTemplate、analysis-report-v1
├── sanitize.py              # Markdown/链接/图片安全过滤
├── html.py                  # canonical Markdown 到安全 HTML
├── service.py               # 编排、原子写入、格式隔离、存储登记
└── word.py                  # 现有 DOCX renderer，必要时补充 renderer 适配
```

Agent 只修改报告组装和服务调用边界；不把 HTML 解析、路径校验或模板字符串重新放回 Agent。

## 测试策略

新增离线测试：

- `tests/reports/test_models.py`：模型字段、任务隔离、模板版本和 JSON 序列化；
- `tests/reports/test_sanitize.py`：危险 HTML、链接协议、数字待确认和图片引用过滤；
- `tests/reports/test_html.py`：标题、表格、列表、代码块、链接、图片和占位渲染；
- `tests/reports/test_service.py`：模板替换、Markdown/HTML/DOCX 结果、原子输出和 Artifact 登记；
- `tests/reports/test_service_failures.py`：HTML/DOCX/存储失败互不影响；
- `tests/agent/test_report_service_integration.py`：Agent 兼容字段、Word 失败保留 Markdown 和 HTML 输出；
- 现有 Word、报告契约、M00-M10 全量测试必须继续通过。

验证命令：

```powershell
E:\anaconda\python.exe -m pytest -q
E:\anaconda\python.exe -m compileall -q src
git diff --check
```

测试不得调用真实模型 API、网络、PDF 工具或生产对象存储。

## 验收映射

| 验收标准 | 设计保证 |
| --- | --- |
| 报告生成失败不影响分析结果 | `ReportBundle` 按格式隔离，Agent 保留分析字段 |
| Markdown、DOCX、HTML 内容一致 | canonical Markdown 作为共同内容基准 |
| 图片全部来自当前任务 | `ChartArtifact` 任务校验、路径边界和存在性检查 |
| 数字与结构化结果一致 | Verified-only 指标注入和 evidence validation |
| 缺失图片有明确占位 | 三种 renderer 统一使用图片不可用占位 |
| 模板可以独立替换 | `ReportTemplate` Protocol 和版本化模板 |
