# M09 调研与发现

- 当前 `src/data_analysis_agent/execution/code_executor.py` 使用进程内 IPython，允许 `os`、`requests`、`pickle` 等高风险能力，没有进程/内存/超时边界。
- `DataAnalysisAgent._analyze_impl()` 目前创建 `CodeExecutor`，通过 `set_variable()` 注入会话目录和数据集 loader，旧 `_process_action()` 通过 `execute_code()` 执行代码。
- M08 已经提供稳定的 `AgentOrchestrationError`、任务状态和 `LegacyAnalysisAdapter`；M09 应通过执行端口接入，避免重写状态机。
- `pyproject.toml` 当前没有 Docker SDK 依赖；容器后端将通过可注入 runtime 抽象与 Docker CLI 适配，测试不依赖真实 Docker。
- 配置已有 `APP_ENV`、类型化 `Settings` 和 production fail-fast 逻辑；执行模式和资源限制应纳入同一配置对象。

