# M09 调研与发现

- 当前 `src/data_analysis_agent/execution/code_executor.py` 使用进程内 IPython，允许 `os`、`requests`、`pickle` 等高风险能力，没有进程/内存/超时边界。
- `DataAnalysisAgent._analyze_impl()` 目前创建 `CodeExecutor`，通过 `set_variable()` 注入会话目录和数据集 loader，旧 `_process_action()` 通过 `execute_code()` 执行代码。
- M08 已经提供稳定的 `AgentOrchestrationError`、任务状态和 `LegacyAnalysisAdapter`；M09 应通过执行端口接入，避免重写状态机。
- `pyproject.toml` 当前没有 Docker SDK 依赖；容器后端将通过可注入 runtime 抽象与 Docker CLI 适配，测试不依赖真实 Docker。
- 配置已有 `APP_ENV`、类型化 `Settings` 和 production fail-fast 逻辑；执行模式和资源限制应纳入同一配置对象。
- Task 5 review 发现：直接构造 Agent 可能绕过 production settings；compatibility `files` 依赖 ambient settings；输入 staging cleanup 失败会被吞掉；越界图片路径清空后仍会进入收集；Python prelude 的 JSON `true`/`null` 不是 Python 字面量。Task 6 已分别通过环境 settings、session settings、可重试 CleanupFailureError、丢弃 invalid figure 和 `repr()` 修复。
- `ExecutionAudit` 只暴露 task/backend/hash/timestamps/duration/status/limit/error/files 元数据；源代码、宿主路径、异常原文和 secret 不进入审计 payload。

## M10 发现

- `MetricArtifact` 和 `ChartArtifact` 已存在，但目前只有通用文件/工具来源字段，没有 task、数据集、执行 ID、代码哈希和验证状态。
- `ExecutionAudit` 目前没有 `execution_id`；`AgentExecutionSession` 每次执行都会生成审计记录，适合在该边界补充稳定执行引用。
- `DataAnalysisAgent` 仍从兼容字典收集图表，最终报告提示词由 `_build_final_report_prompt()` 生成；证据上下文应在此处注入而不是解析普通 stdout。
- M05 的 `_staged_file()` 和 M09 的执行输出路径解析已经提供任务输出边界；M10 的图表检查应复用相同的解析后路径规则。
- 现有领域模型是冻结、拒绝未知字段、递归 JSON 安全序列化的 Pydantic 模型；新增证据模型必须沿用 `DomainModel`，保持旧构造方式可用。

## M10 收尾验证

- M10 Task 4 后的初次全量回归为 `6 failed, 761 passed, 3 skipped`；6 个失败均发生在报告生成兼容测试：
  `tests/test_word_report_integration.py` 的 3 个用例、`tests/contract/test_report_contract.py` 的 2 个用例，以及
  `tests/llm/test_agent_structured_boundary.py::test_structured_report_error_propagates_after_markdown_fallback`。
- 共同根因是旧测试夹具通过 `object.__new__(DataAnalysisAgent)` 构造对象，未初始化 `task_id`；M10 报告证据上下文首次访问该属性时提前抛出 `AttributeError`，遮蔽了 Markdown/Word 和 LLM 错误契约。
- 修复位于 `DataAnalysisAgent._get_evidence_registry()`：通过 `getattr` 兼容缺失的 `task_id`，按需生成 UUID 并保存到 Agent，再创建任务级 `EvidenceRegistry`。正常构造路径仍复用已有任务 ID。
- 修复后的模块方式全量验证为 `767 passed, 3 skipped, 18 warnings`。在 Windows 上直接调用 `Scripts\\pytest.exe` 会因启动器路径优先级导致 `tests.fixtures` 收集冲突；使用同一解释器的 `E:\\anaconda\\python.exe -m pytest -q` 可稳定复现项目验证结果。

## M11 初步调研

- `src/data_analysis_agent/reports/word.py` 已经是可复用的 Word 渲染实现；根目录 `utils/word_report_generator.py` 只是兼容导出，不应继续承载新的报告逻辑。
- `DataAnalysisAgent._generate_final_report()` 仍同时负责模型请求、Markdown 文件写入、Word 转换、存储登记和兼容结果组装；M11 应把这些报告产物职责移入独立服务，保留 Agent 的兼容返回字段。
- 当前 Word 渲染器已覆盖标题、段落、粗体/斜体、列表、代码块、图片和缺失图片占位，并限制图片位于当前会话输出目录；HTML 尚无统一渲染器或安全过滤入口。
- M10 的 `MetricArtifact`、`ChartArtifact` 和报告数字校验可作为报告服务的结构化输入；报告服务不应重新从模型文本猜测数字，也不应接受任意宿主路径作为图片来源。
- `ArtifactStorageService` 已能登记报告文件元数据和下载 URL；M11 可以复用该端口，不需要重新设计文件存储。

## M11 已确认设计方向

- 采用 `ReportService + ReportDocument`：Agent 保留模型叙述调用，报告服务负责结构化内容注入、模板渲染、Markdown/HTML/DOCX 产物和存储登记；PDF 只保留扩展点。
- 报告服务只接受已验证指标和当前任务已登记的图表；模型叙述中的无证据数字标记为待确认，图片路径不直接信任模型输出。
- 每种输出格式返回独立结果；Markdown/HTML/DOCX 失败互不回滚，Word 失败继续保留 Markdown，并复用现有 `ArtifactStorageService` 登记接口。
- 测试将覆盖 ReportDocument 校验、Markdown/HTML/DOCX 内容一致性、图片边界和缺失占位、Markdown 安全过滤、格式失败隔离、可替换模板，以及 Agent 兼容字段；所有测试继续使用 fake LLM/renderer，不访问真实模型或网络。
