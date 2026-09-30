# M18 日志、监控与成本统计调研

## 当前系统边界

- API 已通过 `request_id_middleware` 生成或透传 `X-Request-ID`，但请求完成、异常和业务阶段没有统一结构化日志上下文。
- `AnalysisTask`/`analysis_tasks` 已记录 `model_call_count` 和 `model_duration_ms`，但没有按模型、Token、费用或单次调用明细的持久化记录。
- LLM 网关的 `LLMCallMetrics` 已包含 provider、model、attempt、耗时、usage、estimated_cost_usd 和 provider request_id；`ChatRequest.metadata` 可承载调用关联上下文。
- Worker 已通过 `TaskEvent` 记录阶段转换并在 `WorkerResult` 计算总执行时长；异常只保留安全消息，缺少结构化错误堆栈记录。
- 容器执行结果已包含执行耗时、超时、资源限制和输出限制标记；容器 runtime 当前没有统一的资源采样快照接口。
- 现有 `AuditWriter` 是安全的用户操作审计边界，不应把高频性能明细或模型 prompt/response 写入 AuditEvent。
- APIApplication 已集中组装数据库、任务服务和审计依赖，适合在此处注入 observability service；API 认证主体可用于 user_id 过滤和管理员查询。

## 已确认的范围

- M18 第一版需要持久化观测明细，并提供按任务/模型查询的 API。
- 结构化 JSON 日志与持久化明细使用相同的 request_id/task_id/user_id/stage/tool_name/model_name 关联字段。
- 不记录 prompt、response、源代码、密钥、宿主绝对路径或完整未经清洗的用户输入。
- 数据库明细必须支持任务链路还原、模型调用次数、失败率、平均任务耗时、Token 与模型成本聚合。

## 方案候选

### 方案 A：复用 TaskEvent/AuditEvent metadata

把 LLM、Worker、沙箱事件全部追加到现有任务事件或审计 metadata 中，再从 JSON 聚合。

优点是迁移少、接入快；缺点是事件语义混杂、Token/费用查询依赖 JSON 扫描、明细约束弱，无法清晰区分业务审计与调试/性能观测。放弃。

### 方案 B：专用观测明细表与查询服务（推荐）

新增结构化 `observability_events`、`llm_call_records`、`sandbox_execution_records` 三类持久化记录；日志只输出 JSON，查询服务负责任务链路、单次模型调用和聚合指标。记录接口使用可替换 writer，未来可映射到 OpenTelemetry span、Prometheus counter/histogram 或 Sentry event。

优点是字段可校验、查询高效、业务审计边界清晰，且能保存模型成本和沙箱资源快照；代价是新增迁移、仓储、服务和 API 契约。

### 方案 C：单一通用 telemetry JSON 表

所有事件用 `kind + payload_json` 写入一张表。

优点是扩展最快；缺点是核心统计字段不可索引/约束，查询和成本复算更脆弱，后续接 Prometheus/OTel 仍需再做语义映射。不作为第一版。

## 推荐决策

采用方案 B。保留 `AuditEvent` 处理用户操作审计，新增 observability 端口处理高频运行时事件；`LLMCallMetrics` 作为模型调用事实来源，Worker/执行器在各自边界写入耗时和错误/资源字段。查询 API 默认按当前用户隔离，管理员可查询全局并沿用现有跨用户访问审计。
