# M19 调研发现

- 当前 HEAD 是 M18 观测设计提交 51ab93f，工作区已有用户未跟踪目录 worktrees/，不触碰。
- M09 已提供容器执行的输入只读挂载、输出边界、网络策略、资源限制、超时和清理契约；测试可注入 FakeRuntime，无需 Docker。
- M10 已提供 EvidenceRegistry 的指标复算、图表路径校验和报告数字校验；M19 应补充固定评测入口和报告化结果，而不是改变证据语义。
- 数据上传边界由 DatasetUploadService 与 CsvInspector 负责大小、编码、CSV 结构和重复列名校验。
- LLM 结构化边界由 LLMClient/LLMHelper 负责 JSON/Pydantic 校验；现有 Fake Provider 可重放非法 JSON 与连续失败。
- 工具边界由 ToolExecutor 负责任务上下文、权限、网络和超时校验；资源边界由 AuthorizationService 负责 owner/admin 访问判断。
- 现有 pytest 配置为 tests/，依赖已支持 pytest、pytest-asyncio、httpx；测试不要求真实 API。

