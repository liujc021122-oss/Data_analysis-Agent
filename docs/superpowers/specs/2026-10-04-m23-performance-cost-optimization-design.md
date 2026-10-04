# M23 性能与成本优化设计

## 状态

已完成设计评审。M23 拆分为三个可独立验证的阶段，第一阶段先落地资源预算与成本计量。

## 目标

系统稳定运行后，降低大型数据集、并发任务和模型调用的资源消耗，同时保持任务隔离、结果可追踪和现有 Agent/Worker/API 兼容性。

M23 必须满足：

- 大文件不会直接进入模型上下文；
- 模型只接收受限的 Schema、统计摘要、采样摘要和执行结果摘要；
- 重复的数据剖析和相同问题分析可以命中缓存；
- 用户并发任务数、单任务模型调用次数和图表资源受限；
- 每个任务可查询 token、耗时和估算成本；
- 不同任务可以并行执行，不因一个任务的预算或缓存锁阻塞全部任务；
- 超出配额时返回稳定、明确且不泄露敏感信息的错误。

## 范围拆分

M23 不是一个一次性修改，而是三个相互衔接、各自可验收的阶段：

### M23-A：资源预算与成本计量

本阶段建立统一资源边界：

- 用户活动任务配额；
- 单任务模型调用次数；
- 模型输入/输出 token、耗时和估算成本累计；
- 图表数量、单文件大小和总图表字节数限制；
- API、Worker 和 Agent 的稳定配额错误契约。

本阶段不引入新的 Redis 计数协议，不改变消息只携带 `task_id` 的 Worker 约束。

### M23-B：数据边界与缓存

本阶段降低大数据集和执行结果进入模型上下文的成本：

- 大文件优先使用已有 DuckDB 依赖执行扫描和聚合；
- 小文件继续允许 pandas/现有兼容路径；
- 生成受限 Schema、统计摘要和可选采样摘要；
- 对执行 stdout、变量和表格结果生成摘要；
- 上下文组装器按 token/字符预算裁剪输入；
- 以数据集校验和、剖析版本和策略版本为键缓存剖析结果。

本阶段不把完整数据、完整 stdout 或不受限的 DataFrame 序列化传给模型。

### M23-C：模型路由与分析缓存

本阶段降低重复分析和高价模型使用：

- 根据任务复杂度选择模型；
- 探索和结构化数据发现优先使用低成本模型；
- 最终报告使用配置的强模型；
- 对同一用户、同一数据集版本、同一问题和同一策略版本的成功分析结果缓存；
- 缓存命中直接复用安全的报告/产物引用，不再次发起模型调用；
- 记录缓存命中、节省调用数和估算节省成本。

## 设计原则

1. 数据库是任务状态、配额事实和累计成本的权威来源。
2. 进程内对象只做当前任务的快速预算检查，不做跨进程的全局计数事实。
3. 任务配额检查发生在幂等判定之后，重复提交不消耗新配额。
4. 预算错误是稳定业务错误，不自动触发 Worker 重试。
5. 所有缓存键都包含数据版本和策略/提示词版本，避免旧摘要或旧报告污染新任务。
6. 缓存和摘要失败时不伪造结果；可以安全降级为未命中或任务失败，但不能把完整数据交给模型作为兜底。
7. 现有 `LLMCallMetrics`、`OrchestratorLimits`、`ExecutionLimits`、`ToolExecutor` 和任务生命周期继续作为接入边界。

## M23-A 资源预算与成本计量

### 资源策略

新增不可变的 `ResourcePolicy` 配置对象，由 API、Worker、Agent 和执行器共享。它至少提供：

```python
class ResourcePolicy(Protocol):
    def check_active_task(self, user_id: UUID) -> None: ...

    def check_model_call(self, task: AnalysisTask, *, model: str) -> None: ...

    def check_chart_output(
        self,
        task: AnalysisTask,
        *,
        count: int,
        file_bytes: int,
        total_bytes: int,
    ) -> None: ...
```

每个运行中任务绑定一个 `TaskBudget`。它只保存当前任务的已使用资源快照和策略引用，不持有跨任务锁。模型调用前检查调用预算，模型调用完成后记录实际指标；图表输出收集前检查数量和字节预算。

### 提交路径

`TaskSubmissionService` 的提交顺序为：

1. 在现有幂等事务中查找用户和幂等键；已存在的相同请求直接返回原任务。
2. 对用户记录加数据库事务锁，并统计活动状态任务：`PENDING`、`QUEUED`、`RUNNING`、`EXPLORING`、`CLEANING`、`ANALYZING`、`VALIDATING`、`REPORTING`。
3. 活动任务数未超限时创建并入队任务；终态任务不占用配额。
4. 超限时不创建任务、不发送消息，抛出 `TASK_CONCURRENCY_LIMIT`。

使用用户记录作为锁点，避免“当前没有活动任务时两个请求同时通过计数检查”的竞态。SQLite 测试环境依靠单写事务工作，生产数据库使用行锁语义。

取消、失败和完成通过任务状态转为终态自然释放配额，不维护容易漂移的独立计数器。失败任务重试前重新检查活动任务配额。

### LLM 调用与成本累计

现有 `LLMClient` 保持 `CallRecorder.record(metrics)` 接口。Worker/Agent 为每个任务创建绑定 recorder：

```python
class TaskUsageRecorder(Protocol):
    def record_llm_call(
        self,
        task_id: UUID,
        metrics: LLMCallMetrics,
    ) -> None: ...
```

`record_llm_call()` 通过 Repository 原子更新任务累计字段：

- `model_call_count`：一次逻辑 LLM 请求计数；结构化输出修正请求单独计数；
- `model_duration_ms`：调用耗时累计；
- `model_input_tokens`：输入 token 累计；
- `model_output_tokens`：输出 token 累计；
- `model_total_tokens`：总 token 累计；
- `estimated_model_cost_usd`：按 `LLMConfig.model_prices` 计算的成本累计。

Provider 的重试次数保留在单次 `LLMCallMetrics.attempt_count` 中；如果 Provider 返回 token 使用量，则使用真实值，否则保留现有估算标记。数据库不保存 prompt、完整响应、请求参数、认证材料或请求 URL。

模型调用预算检查必须发生在 Provider 调用之前。调用限制覆盖 `AgentLLMPort` 触发的普通请求和结构化输出修正请求；Provider 内部重试继续受已有 `max_attempts` 限制。

现有 `OrchestratorLimits.max_model_calls` 保留兼容语义，并由统一任务策略提供相同或更严格的上限，避免 Agent 编排器和 LLM 客户端各自放宽预算。

### 图表资源

现有执行器的输出文件边界继续负责路径、文件数量和总输出字节安全；M23-A 增加图表专属预算：

- 图表数量；
- 单个图表文件字节数；
- 当前任务图表总字节数。

超限图表在输出收集阶段被拒绝，记录 `TASK_CHART_RESOURCE_LIMIT` 安全摘要，不把宿主路径写入错误。图表超限只作为报告警告，报告继续生成并明确缺少该图表；模型调用预算超限才终止任务。

### 默认配置

```text
max_active_tasks_per_user = 3
max_model_calls_per_task = 20
max_chart_count = 20
max_chart_file_bytes = 10 MiB
max_chart_total_bytes = 50 MiB
max_model_cost_usd_per_task = disabled by default, configurable
```

成本上限未配置时仍必须记录估算成本；只有明确配置后才执行成本拒绝。生产配置必须对启用的限制使用有限、非负值，应用启动时校验。

### 错误契约

资源错误使用结构化的稳定字段：

```text
code
resource
limit
observed
retryable
message
```

错误类型：

- `TASK_CONCURRENCY_LIMIT`：用户活动任务数超限，API 返回 HTTP 429；
- `TASK_MODEL_CALL_LIMIT`：任务模型调用数超限，任务失败且不可自动重试；
- `TASK_MODEL_COST_LIMIT`：任务成本预算超限，任务失败且不可自动重试；
- `TASK_CHART_RESOURCE_LIMIT`：图表产物被拒绝，报告继续生成并保留安全警告。

错误消息只描述资源类型、限制和当前观察值，不包含 prompt、响应正文、宿主路径、连接信息或密钥。

### M23-A API 视图

任务详情增加只读 usage/cost 摘要：

```json
{
  "model_call_count": 3,
  "model_input_tokens": 1200,
  "model_output_tokens": 480,
  "model_total_tokens": 1680,
  "model_duration_ms": 4200,
  "estimated_model_cost_usd": 0.0012
}
```

API 不返回单次 prompt、模型输出或认证信息。事件和失败详情只返回稳定错误码和受限资源字段。

## M23-B 数据边界与缓存

### 数据执行路径

新增统一的数据摘要端口，输入是任务授权后的数据集引用，不接受任意宿主路径：

```python
class DatasetSummaryService(Protocol):
    def summarize(self, dataset: DatasetRecord, policy: SummaryPolicy) -> DatasetSummary: ...
```

当数据集大小、行数或格式达到配置阈值时，使用 DuckDB 进行列式扫描、聚合和受限采样；小文件可继续使用 pandas 兼容路径。摘要服务只返回结构化摘要，不返回完整行集。

`DatasetSummary` 至少包含：文件格式、列名和类型、行数、空值计数、受限 distinct 计数、数值范围/分位数和固定上限的采样行。采样数量、列数、字符串长度和总摘要字节数都由 `SummaryPolicy` 限制。

### 上下文组装

新增 `ContextBudget`/`ContextAssembler`：

- 固定保留任务问题、Schema 和摘要元数据；
- 按优先级裁剪采样值、低优先级统计量和历史执行细节；
- 在发送给模型前估算输入 token；
- 超过预算时只缩减摘要，不把原始数据作为兜底输入；
- 把被裁剪字段记录为计量信息，便于解释结果不完整。

执行结果摘要只保留状态、shape/row count、列名、受限统计、前后若干行和安全错误码；stdout、变量和 DataFrame 不完整传入模型。

### 剖析缓存

剖析缓存键由以下字段组成：

```text
dataset checksum
dataset size/content type
summary policy version
summary implementation version
```

缓存只保存结构化、受限的 `DatasetSummary` 和生成时间/大小元数据。缓存读写失败按未命中处理，不阻塞其他任务，也不把完整数据写入缓存。数据集校验和变化或策略版本变化会自然失效旧缓存。

## M23-C 模型路由与分析缓存

### 模型路由

新增 `ModelRoutingPolicy`，根据数据集数量、Schema 宽度、摘要大小、问题复杂度、工具调用需求和当前阶段选择模型：

- `EXPLORATION`、Schema 探索和低风险摘要优先选择配置的低成本模型；
- `ANALYSIS` 根据复杂度选择低成本或标准模型；
- `REPORTING` 使用配置的强模型；
- 未配置专用模型时回退到现有默认模型，不改变旧调用行为。

实际使用的模型继续写入 `LLMCallMetrics.model`，成本按实际模型价格计算。路由决策本身不把完整数据或敏感内容写入审计。

### 分析结果缓存

成功分析结果的缓存键至少包含：

```text
owner/user scope
normalized query
ordered dataset checksums
summary policy version
prompt/template version
model routing version
```

只缓存成功完成的、已通过报告和产物边界校验的结果。失败、取消、外部依赖失败和包含不可复用临时路径的结果不进入缓存。命中时复用已授权的报告/Artifact 引用，重新校验所有者和文件边界；不能把另一个用户的结果直接暴露给当前用户。

缓存命中不发起模型调用，并在任务 usage 中标记 `cache_hit=true`、`model_calls_saved` 和 `estimated_cost_saved_usd`。缓存服务不可用时按未命中执行，不阻塞新任务。

## 并发与故障边界

- 每个任务使用自己的 Agent、执行会话、预算对象和输出目录；任务之间不共享可变对话历史。
- 数据库原子更新只锁定当前任务行；用户并发配额只在提交事务内锁定用户记录，不在整个分析期间持锁。
- 缓存使用短事务和按键粒度的读写，不使用全局进程锁。
- 配额错误不可重试；网络、模型供应商和 Worker 既有可重试错误继续遵循原有策略。
- 计量写入失败不能泄露敏感信息；若无法确认预算状态，模型调用边界应 fail-closed，任务返回稳定资源错误。

## 验收矩阵

| 验收标准 | 设计验证 |
| --- | --- |
| 大文件不会直接撑爆模型上下文 | DuckDB 摘要、受限采样、ContextBudget 和执行结果摘要测试 |
| 重复任务不会重复消耗大量模型调用 | 剖析缓存和成功分析缓存命中测试，确认 Provider 调用次数不增加 |
| 可以统计单任务成本 | LLM 指标、数据库原子累计、任务详情 API 和多模型计价测试 |
| 并发任务不会互相阻塞 | 多任务 Worker/SQLite 集成测试、用户配额仅提交事务加锁测试 |
| 超出配额时能够明确提示用户 | API 429、任务失败错误码、图表警告和资源字段测试 |

## 兼容性与非目标

- 默认未启用 M23 专用缓存或路由时，现有 Agent、LLM、Worker、工具和兼容入口保持行为不变。
- M23 不新增 Polars 依赖；若后续基准测试证明 DuckDB 不足，再单独评估 Polars。
- M23 不把完整数据、完整提示词或模型原文持久化为缓存或审计。
- M23 不改变 M22 的 MCP 工具白名单、只读 SQL 和工具审计契约。
- M23 不要求真实外部模型、Redis、MCP Server 或企业数据源才能运行离线测试。
