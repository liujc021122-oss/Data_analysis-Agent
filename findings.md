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
