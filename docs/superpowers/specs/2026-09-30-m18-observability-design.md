# M18 日志、监控与成本统计设计

## 背景

系统已经具备 API `request_id`、任务状态事件、Worker 阶段事件、LLM 调用指标和沙箱执行审计，但这些信息还没有统一的关联上下文、持久化模型和查询接口。M18 需要让一次分析可以按任务 ID还原完整链路，定位失败阶段，并统计任务耗时、模型调用、Token 和模型成本。

## 目标与范围

第一版包含：

- 结构化 JSON 日志；
- 业务日志与调试日志分离；
- API、Worker、LLM 和沙箱共享 `request_id/task_id/user_id` 关联字段；
- 持久化运行时观测明细；
- LLM 请求耗时、重试次数、Token 和估算成本；
- Worker 总耗时和阶段耗时；
- 错误类型、安全错误消息和截断后的错误堆栈；
- 沙箱资源限制和 runtime 返回的资源快照；
- 按任务还原链路；
- 成功率、失败率、平均任务耗时、模型调用次数、Token 和按模型成本统计；
- 普通用户和管理员权限隔离。

第一版不包含：

- OpenTelemetry exporter；
- Prometheus/Grafana 服务部署；
- Sentry SDK 接入；
- Prompt、完整模型响应、源代码、密钥、Cookie、数据库 URL、宿主绝对路径或未经清洗的用户输入持久化；
- 独立的可视化监控页面。

## 方案选择

### 方案 A：复用 TaskEvent/AuditEvent metadata

将 LLM、Worker 和沙箱记录追加到既有任务事件或审计 metadata 中。实现成本最低，但业务审计、阶段事件和性能明细语义混杂，Token/成本聚合依赖 JSON 扫描，字段约束和索引不足，因此不采用。

### 方案 B：专用观测明细表与查询服务

新增通用观测事件、LLM 调用和沙箱执行三类记录；日志以 JSON 输出，查询服务负责任务链路和统计聚合。现有 AuditEvent 继续只处理用户操作审计。该方案能为核心统计字段提供约束和索引，并为后续 OpenTelemetry、Prometheus 和 Sentry 保留稳定 writer/recorder 端口。

### 方案 C：单一通用 telemetry JSON 表

所有事件以 `kind + payload_json` 写入一张表。扩展速度快，但核心统计字段不可直接约束和索引，查询与成本复算更脆弱，不采用。

采用方案 B。

## 架构

### 关联上下文

新增 `ObservationContext` 和基于 `contextvars` 的上下文管理器，字段为：

```python
ObservationContext(
    request_id: str | None,
    task_id: UUID | None,
    user_id: UUID | None,
    stage: str | None,
    tool_name: str | None,
    model_name: str | None,
)
```

API middleware 创建或透传 `X-Request-ID` 并绑定 `request_id`。认证依赖补充 `user_id`。任务创建把根 `request_id` 写入任务内部关联字段。Worker 的 broker payload 仍只包含 `task_id`；Worker 领取任务后从数据库读取任务所有者和根请求 ID，再绑定上下文。Agent 阶段、LLM 调用和沙箱执行在各自边界绑定 `stage/tool_name/model_name`。

LLM 请求 metadata 可以携带观测关联信息，但不得包含 prompt 或 response；LLM provider 返回的请求 ID单独命名为 `provider_request_id`，不覆盖系统 `request_id`。

### JSON 日志

使用标准库 `logging` 和自定义 JSON formatter，不新增日志依赖。每条结构化日志包含以下稳定字段，未设置的关联字段为 `null`：

```json
{
  "timestamp": "2026-09-30T00:00:00+00:00",
  "level": "INFO",
  "channel": "business",
  "component": "worker",
  "event_type": "stage_finished",
  "request_id": "request-1",
  "task_id": "...",
  "user_id": "...",
  "stage": "ANALYZING",
  "tool_name": null,
  "model_name": null,
  "duration": 12.5,
  "token_usage": null,
  "error_type": null
}
```

`duration` 的单位固定为毫秒。持久化模型使用明确的 `duration_ms` 字段。`token_usage` 使用 `{input_tokens, output_tokens, total_tokens, estimated}` 结构。

`business` channel 包含任务生命周期、阶段、模型调用、沙箱结果和错误；关键业务事件持久化。`debug` channel 只输出 JSON 日志，默认不进入业务审计表。异常 formatter 只写清洗、截断后的堆栈。

### Writer 端口

定义可替换的观测写入端口：

```python
class ObservabilityWriter(Protocol):
    def record_event(self, event: ObservationEvent) -> None: ...
    def record_llm_call(self, call: LLMCallObservation) -> None: ...
    def record_sandbox_execution(
        self, execution: SandboxObservation
    ) -> None: ...
```

生产实现使用数据库 repository 和 JSON logger；测试使用内存 writer。`BestEffortObservabilityWriter` 包装实际 writer：观测写入失败只生成安全的 `OBSERVABILITY_WRITE_FAILED` 诊断日志，不向 API、Worker、LLM 或沙箱调用方抛出异常。

## 持久化模型

### 任务根关联

在 `analysis_tasks` 增加可空的内部 `request_id` 字段。API 创建任务时写入当前请求 ID；旧任务或非 API 直接创建的任务保持 `null`，Worker 对这类任务使用固定格式 `task:<task_id>` 的 task-scoped fallback request ID。该字段不加入公开任务 DTO。

### `observability_events`

字段：

- `event_id`；
- `request_id`、`task_id`、`user_id`；
- `component`、`event_type`、`level`、`channel`；
- `stage`、`tool_name`、`model_name`；
- `duration_ms`、`success`；
- `token_usage_json`；
- `error_type`、清洗后的 `error_message`、清洗并截断的 `error_stack`；
- `metadata_json`；
- `occurred_at`。

建立 `(task_id, occurred_at, event_id)`、`(request_id, occurred_at)`、`(user_id, occurred_at)`、`(component, event_type, occurred_at)`、`(model_name, occurred_at)` 和 `(stage, occurred_at)` 索引。metadata 只接受 JSON-safe 的 allowlisted 数据。

### `llm_call_records`

一次模型请求一行，字段包含：

- `call_id`、`request_id`、`task_id`、`user_id`、`stage`、`tool_name`；
- `provider_name`、`model_name`、`provider_request_id`；
- `attempt_count`、`started_at`、`finished_at`、`duration_ms`；
- `input_tokens`、`output_tokens`、`total_tokens`、`usage_estimated`；
- `estimated_cost_usd`、`success`；
- `error_type`、`error_message`、`error_stack`。

建立 `(task_id, started_at)`、`(request_id, started_at)` 和 `(model_name, started_at)` 索引。已存在的 `LLMCallMetrics` 继续作为网关事实来源；观测适配器补充系统关联上下文和失败状态。

### `sandbox_execution_records`

使用现有 `ExecutionAudit.execution_id` 作为记录 ID，字段包含：

- `execution_id`、`request_id`、`task_id`、`user_id`、`stage`、`tool_name`；
- `backend`、`started_at`、`finished_at`、`duration_ms`；
- `success`、`exit_code`、`timed_out`、`resource_limited`；
- `limits_json`、`resource_usage_json`；
- `error_type`、`error_message`、`error_stack`。

建立 `(task_id, started_at)` 和 `(request_id, started_at)` 索引。`resource_usage_json` 保存 runtime 能提供的 CPU、内存、PID、输出字节等快照；缺失值为 `null`，不伪造为零。代码只保存哈希。

## 运行链路

```text
API request
  -> request_id middleware
  -> task creation + root request_id
  -> broker(task_id only)
  -> Worker loads user_id/request_id
  -> stage context and duration
  -> LLM call record
  -> sandbox execution record
  -> terminal task summary
```

API middleware输出请求开始/结束和未处理异常事件。任务服务输出创建、入队、取消、重试和持久化失败事件。Worker在 `process()` 周围记录总耗时，在阶段回调记录开始/结束和错误。LLM gateway在成功、重试耗尽、超时、鉴权失败和结构化输出失败边界记录调用。执行器在 `ExecutionAudit` 生成处写入沙箱记录，并将 runtime 资源快照复制到结果模型。

观测写入不会改变业务结果。原有任务状态、TaskEvent、AuditEvent 和 LLM 公共响应契约继续有效。

## 统计与查询 API

### 任务链路

`GET /api/observability/tasks/{task_id}` 返回：

- 任务 ID、所有者和根请求 ID；
- 按发生时间排序的观测事件；
- LLM 调用明细；
- 沙箱执行明细；
- 任务状态、终态、总耗时、模型调用次数、Token 和成本摘要。

### 聚合指标

`GET /api/observability/metrics` 支持时间范围、模型和任务状态过滤，返回：

- 任务总数、完成数、失败数、取消数；
- 成功率、失败率、取消率；
- 平均任务耗时；
- 模型调用次数和 Token 总量；
- 按模型分组的调用次数、Token、成本、失败数和未计价调用数。

成功率定义为 `COMPLETED / (COMPLETED + FAILED)`，取消单独统计。平均任务耗时只计算已进入终态的任务，从创建事件到终态事件。成本只累加有价格配置且 Token 可用的调用；未知价格调用计入 `unpriced_call_count`。

普通用户只能查询自己拥有的任务和指标；管理员可查询全局。管理员读取跨用户数据时复用 `ADMIN_CROSS_USER_ACCESS` 审计。无权限和不存在任务使用统一 not-found 契约。

## 错误与隐私处理

- `error_type` 使用稳定错误码或异常类型名；
- `error_message` 复用现有异常清洗工具；
- `error_stack` 通过清洗后最多保留 8 KB；
- 不持久化 prompt、完整响应、源代码、Cookie、密钥、数据库 URL、宿主路径或未经清洗的用户输入；
- 每个 JSON 字段经过长度和 JSON-safe 校验；
- 模型 provider 请求 ID与系统 request ID分开存储；
- 观测查询不能绕过现有的资源 owner/admin 授权边界。

## 测试策略

### 单元测试

- 观测模型拒绝未知字段、负耗时、负 Token 和非 JSON metadata；
- contextvars 在嵌套绑定和异常退出后恢复原值；
- JSON formatter 输出固定字段并区分 business/debug channel；
- 错误消息和堆栈清洗，验证 secret、路径和 prompt 不泄露；
- 模型价格、估算 Token、成功率、平均耗时和未知价格统计公式；
- runtime 资源快照映射到 `ExecutionResult`/`ExecutionAudit`。

### 集成测试

- fake LLM provider 成功、重试、失败和缺少 usage 的调用均产生正确的 LLM 记录；
- fake Worker 记录阶段耗时、重试和失败堆栈；
- fake sandbox runtime 记录限制和资源快照；
- 数据库 repository 和 Alembic migration 支持新表、索引、重复升级；
- API 查询可按任务返回 API、Worker、LLM 和沙箱的关联链路；
- 普通用户无法读取其他用户观测数据，管理员查询会产生跨用户审计。

### 回归验证

- M18 聚焦 API/Worker/LLM/execution/database/config 测试；
- 全量 `pytest`；
- `compileall -q src`；
- editable install；
- SQLite Alembic upgrade head，并重复执行 upgrade；
- `git diff --check`；
- README 示例和 OpenAPI 路径契约。

## 后续扩展

`ObservabilityWriter` 和稳定字段映射为后续 exporter 边界。OpenTelemetry 可将事件映射为 span，Prometheus 可从聚合服务导出 counter/histogram，Sentry 可消费清洗后的错误事件；这些不改变 M18 的数据库和业务调用契约。
