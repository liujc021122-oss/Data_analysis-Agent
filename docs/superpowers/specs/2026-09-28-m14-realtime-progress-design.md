# M14 实时进度推送设计

## 状态

已由用户确认设计，实施范围为后端事件持久化与 SSE 协议；仓库当前没有前端工程。

## 目标

让分析任务的调用方能够实时看到任务创建、开始、阶段变化、模型/工具调用、图表/报告产出、失败和完成，并能在浏览器或网络断开后从数据库恢复进度。

## 非目标

- 第一版不引入 WebSocket、Redis Pub/Sub 或其他实时消息基础设施。
- 第一版不创建前端 UI；前端只依赖稳定的 REST/SSE 协议。
- 不通过进度事件提供分析代码、原始数据、模型 prompt/response、执行原始输出、存储 URI 或本地路径。

## 现有上下文

项目已经有 `task_events` 持久化表、任务状态机、Worker 阶段回调和分页事件接口。M14 在这套链路上增量扩展：数据库事件仍是事实来源，Worker 和 Agent 通过现有回调链写入事件，API 负责授权、历史回放和 SSE 推送。

## 事件模型

`TaskEvent` 保留现有字段 `event_id`、`task_id`、`event_type`、`from_status`、`to_status`、`message`、`occurred_at` 和 `metadata`，新增：

- `sequence`: 任务内从 1 开始的单调整数，用作 SSE 游标和消息 ID。
- `stage`: 当前分析阶段；任务级事件为空，阶段事件使用现有阶段状态值。
- `progress`: 0 到 100 的数值进度。

`TaskEventType` 保留旧的大写事件类型，新增以下点号事件类型：

| 事件类型 | 产生时机 | 公开摘要 |
| --- | --- | --- |
| `task.created` | 创建任务并写入初始事件 | 无敏感数据 |
| `task.started` | Worker 成功认领任务 | 尝试次数、开始阶段 |
| `stage.started` | 进入一个分析阶段 | 阶段名、阶段进度 |
| `llm.started` | 发起一次模型调用 | 当前阶段、调用序号 |
| `llm.completed` | 模型调用完成或安全失败 | 成功状态、耗时、错误码 |
| `tool.started` | 发起一次工具调用 | 工具名、当前阶段 |
| `tool.completed` | 工具调用完成或安全失败 | 工具名、成功状态、耗时、错误码 |
| `chart.created` | 图表完成存储登记 | artifact ID、格式 |
| `report.created` | 报告完成存储登记 | artifact ID、格式 |
| `task.failed` | 任务进入 `FAILED` | 稳定错误码、安全消息 |
| `task.completed` | 任务进入 `COMPLETED` | 最终进度 100 |

排队、显式重试和取消继续保留原有状态字段与旧事件兼容行为；取消不伪装成失败。旧的 `STATUS_CHANGED`、`TOOL_CALLED`、`EXECUTION_COMPLETED`、`ARTIFACT_CREATED` 和 `ERROR` 仍可被领域内存事件和历史数据使用。

阶段进度使用固定权重，保证同一任务的基础展示稳定：创建/排队 `0`，运行 `5`，探索 `15`，清洗 `30`，分析 `55`，验证 `75`，报告 `90`，完成 `100`。LLM、工具和产物事件沿用当前阶段进度；失败保留失败前的进度。

任务状态转换仍由现有状态机校验。持久化服务在写入状态转换时将创建、开始、阶段进入、失败和完成映射为对应的 M14 事件类型；领域层返回的旧 `STATUS_CHANGED` 事件契约不被破坏。

## 顺序与迁移

新增 Alembic revision 为 `task_events` 增加 `sequence`、`stage` 和 `progress`，并增加 `(task_id, sequence)` 唯一索引以及按任务序号读取的索引。

迁移按 `(task_id, occurred_at, event_id)` 为已有记录回填序号，保证已有历史可以被 SSE 回放。新事件在任务行锁保护下由事件仓储分配下一个序号；任务状态更新和生命周期事件在同一事务内提交。唯一约束作为并发写入的最后保护。

旧记录在公开 DTO 中通过状态和历史 metadata 计算兼容的事件类型、阶段和进度；新记录直接持久化完整字段。分页 `/events` 接口改为按 `sequence ASC` 返回，保持原字段，同时扩展可选的新字段。

## SSE 协议

新增：

`GET /api/analysis-tasks/{task_id}/events/stream`

连接建立时先检查认证主体和任务所有权。权限失败沿用现有 JSON 错误契约，并且不能通过响应区分其他用户任务是否存在。

`Last-Event-ID` 是上一次收到的任务内 `sequence`。没有该请求头时从 0 回放；有该请求头时只发送 `sequence > Last-Event-ID` 的事件，始终按序号升序读取。非法值返回稳定的 400 错误。

每条 SSE 消息使用以下形状：

```text
id: 17
event: stage.started
data: {"event_id":"...","task_id":"...","event_type":"stage.started","timestamp":"...","sequence":17,"stage":"ANALYZING","message":"...","progress":55,"metadata":{}}

```

`timestamp` 是 `occurred_at` 的 UTC JSON 表示；`sequence` 是恢复游标。响应类型为 `text/event-stream`，并设置 `Cache-Control: no-cache`、`X-Accel-Buffering: no` 等响应头。服务端在没有新事件时短暂轮询数据库，并发送 SSE comment 心跳，避免代理超时。

收到并发送 `task.failed` 或 `task.completed` 后，连接正常结束。已经终态的任务也会先回放游标之后的历史，再结束。数据库是回放来源，因此 API 进程或 Worker 重启不会丢失已提交事件；严格使用 `sequence > cursor` 避免重连时重复显示大量历史。

SSE 路由只要求任务持久化服务，不要求配置任务 broker，因此开发环境无 Redis 时仍可以查看和恢复任务进度。

## 事件接入

- Worker 继续负责创建、入队、认领、状态阶段转换、失败和完成事件。
- Agent 编排器在 LLM 调用和工具调用前后通过统一回调发出对应事件。事件只记录工具名、状态、耗时和错误码，不记录参数、代码、prompt、response 或原始输出。
- 图表和报告在实际存储登记成功后发出产物事件，只记录 artifact ID、格式和安全摘要。
- 生命周期事件与任务状态变更使用同一事务。非关键观测事件写入失败时只记录安全日志，不将数据库异常或内部细节发送给客户端；任务最终状态仍由持久化服务决定。

## 安全与展示权限

事件生产端按事件类型使用白名单 metadata；公开 DTO 再递归移除凭据、路径、代码、原始数据和大对象字段，并限制消息和 metadata 大小。禁止的字段包括 API key、token、authorization、password、secret、prompt、response、code、arguments、output、raw data、`file_path`、`source_uri` 和 `storage_uri` 等。

错误事件只公开稳定错误码和可读的安全消息。异常堆栈、供应商响应、密钥、宿主路径和原始执行输出不会进入事件或 SSE。artifact 仍通过现有的任务所有者授权和下载接口访问，进度事件只提供 artifact ID 等摘要。

## 错误处理

- 任务不存在或不属于当前用户：返回现有 `TASK_NOT_FOUND` 契约，不创建流。
- `Last-Event-ID` 非正整数或格式非法：返回 400 和稳定验证错误。
- 持久化查询失败：不把数据库异常写入 SSE；请求阶段返回现有持久化错误，流中断时只安全记录日志。
- 任务失败：发送 `task.failed`，包含稳定错误码和安全消息，然后关闭连接。
- 客户端断开：停止轮询并释放生成器资源，不改变任务状态。

## 测试与验收

新增测试覆盖：

1. 事件模型的点号类型、进度范围、阶段字段和序号约束。
2. 仓储的序号分配、同任务稳定排序、历史回填和唯一约束。
3. 生命周期、LLM、工具、图表、报告、失败和完成事件的写入与映射。
4. metadata、消息和错误内容的 API Key、路径、代码、原始数据和模型文本泄露防护。
5. SSE 首次历史回放、实时追加、`Last-Event-ID` 续传、严格去重、跨用户隔离、失败消息和终态关闭。
6. 旧分页 `/events`、旧领域事件类型、OpenAPI 路径和无 Redis 开发环境兼容性。
7. Alembic 升级/降级、全量 pytest、`compileall` 和 `git diff --check`。

## 交付边界

本阶段交付后端持久化事件、SSE 流、断线续传、权限和测试。前端可直接使用 SSE `event`、`data.sequence` 和 `Last-Event-ID` 实现实时进度与历史恢复，但不在本仓库中新增 UI。
