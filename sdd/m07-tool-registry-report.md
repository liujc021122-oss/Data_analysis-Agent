# M07：工具注册中心

## 完成内容

- 统一了七个内置工具的注册、输入/输出 Schema、权限、网络访问、副作用、风险等级和最大运行时间。
- 工具执行器统一校验上下文、输入、权限、网络策略和输出，并记录可追踪的工具调用结果。
- 审计快照对凭据、文件路径、URI 和 URL 脱敏，并保持快照有界、不可变、可 JSON 序列化。
- 已知 `ToolError` 保留稳定错误码；未知异常仍转换为安全的通用执行错误。
- `inspect_dataset` 改为严格输出模型，不接受未声明字段，也不向 Agent 输出存储路径。
- `ToolDefinition.required_permissions` 在构造时复制为不可变 `frozenset`。
- 同步入口和异步入口使用一致的非阻塞超时线程池语义。
- Agent 保留可选 typed tool bridge，旧的 YAML/action 分析流程不变。

## 测试与验证

- `py -3 -m pytest tests/tools -q`：61 passed。
- 清空 `OPENAI_API_KEY` 与 `DEEPSEEK_API_KEY` 后执行 `py -3 -m pytest -q`：601 passed，2 skipped（异步测试插件未安装）。
- `py -3 -m compileall -q src/data_analysis_agent`：通过。
- 七个内置工具 Schema 离线导出：通过。
- `git diff --check`：通过。
- `py -3 -m pip check`：未通过，报告的是当前 Anaconda 环境已有的第三方依赖冲突，不是本分支新增依赖冲突：
  `pandas-stubs/types-pytz`、`scipy-stubs/optype`、`httpcore/h11`、`ortools/protobuf`、`selenium/typing-extensions`。

测试未调用真实模型 API。
