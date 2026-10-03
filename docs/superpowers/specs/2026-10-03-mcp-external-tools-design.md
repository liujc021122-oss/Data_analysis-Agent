# M22 MCP 外部工具接入设计

## 状态

已完成设计评审。首版采用协议无关的 MCP Client 端口和四个强类型只读工具适配器；不在本任务中引入具体 MCP SDK、HTTP/stdio 传输或真实企业系统连接。

## 目标

在现有 `ToolRegistry`、`ToolExecutor` 和工具调用审计边界内接入外部 MCP 能力，使 Agent 能发现并调用以下只读工具：

- `list_tables`
- `describe_table`
- `run_readonly_sql`
- `get_business_metric`

接入必须满足：工具输入和输出有 Schema；每个工具有独立鉴权；SQL 使用独立参数并限制为只读；查询时间和返回规模受限；调用结果可追踪；外部系统不可用时返回明确且不泄露敏感信息的降级结果。

## 范围

### 本次实现

- 定义最小、协议无关的 `MCPClient` 端口。
- 用 Pydantic 模型定义 MCP 工具发现信息、四个工具的输入和输出。
- 通过固定白名单发现 MCP 工具，不暴露任意远端工具。
- 将四个工具注册进现有 `ToolRegistry`，复用 `ToolExecutor` 的上下文、权限、网络和超时策略。
- 在 MCP 适配层实现只读 SQL 校验、参数绑定校验、查询和结果边界限制。
- 将 MCP 连接、认证、协议和工具未声明错误映射为 `TOOL_DEPENDENCY_FAILED`。
- 让 `AgentOrchestrator` 在允许的阶段发现和调用相应 MCP 工具。
- 通过 Fake MCP Client 完成离线测试，不需要数据库或网络。

### 不在本次实现

- 具体 MCP SDK、HTTP、stdio、WebSocket 或进程管理实现。
- 外部数据库、数据仓库、CRM、ERP 或 BI 系统的凭据管理。
- 任何写入、更新、删除、DDL、DCL、Shell 或文件系统工具。
- 自动切换到未授权的数据源或用空数据伪造外部结果。
- 为工具记录新增数据库表或迁移；持久化审计通过现有 `task_id` 关联任务所有者追踪调用人。

## 设计原则

1. MCP 是外部系统连接边界，不替代现有沙箱和工具执行策略。
2. Agent 只能看到本地定义的四个稳定工具契约，不能动态执行任意远端工具。
3. 权限检查必须发生在调用 MCP Client 之前；远端认证是第二道边界。
4. 外部响应必须重新通过本地输出 Schema 校验，不能直接进入 Agent。
5. 外部失败必须可区分、可审计、不可泄露原始异常或凭据。

## 架构与数据流

```text
MCP Server
   │ tools/list, tools/call
   ▼
MCPClient（协议端口）
   ▼
MCPToolProvider（发现、白名单、Schema、只读策略）
   ▼
ToolRegistry ── ToolExecutor ── Agent / AgentOrchestrator
                                      │
                                      ▼
                              ToolCallRecorder
```

### 组件职责

#### `MCPClient`

`MCPClient` 是由应用注入的最小端口，不保存业务工具逻辑。它提供：

```python
class MCPClient(Protocol):
    def list_tools(self) -> Sequence[MCPToolDescriptor]: ...

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        context: ToolContext,
        timeout_seconds: float,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...
```

`context.user_id`、`context.task_id` 和 `context.permissions` 由适配器传给 Client，供真实传输实现做远端鉴权和追踪。认证材料只存在于 Client 实例，不进入工具参数、提示词或审计快照。

MCP Client 错误使用稳定异常类型区分不可用、认证失败和协议失败；适配器不会把原始异常文本返回给 Agent。

#### `MCPToolProvider`

Provider 负责：

- 调用 `list_tools()` 并校验远端描述；
- 仅接受四个固定名称；
- 将已知远端工具映射到本地静态 Pydantic Schema；
- 为 `ToolRegistry` 注册工具定义和处理器；
- 在处理器内执行只读 SQL、参数和结果边界策略；
- 将外部错误映射为现有 `ToolError` 子类。

Provider 不提供通用的 `mcp_call(server, tool, arguments)` 接口。远端声明的未知工具、写入工具和 Shell 工具不会注册。

调用 `list_tools()` 失败时，Provider 返回结构化的不可用状态；配置了 MCP Client 的工具契约仍可用于 Agent 发现，但实际调用返回 `TOOL_DEPENDENCY_FAILED`。这样 Agent 能区分“工具契约已知”和“外部依赖当前不可用”。

## 工具契约

### 发现模型

```python
class MCPToolDescriptor(BaseModel):
    name: StrictStr = Field(pattern=r"[a-z][a-z0-9_]{0,63}")
    description: StrictStr = Field(min_length=1)
    input_schema: dict[str, JsonValue]
```

Provider 只接受名称完全匹配四个白名单工具的描述。远端 Schema 用于验证描述完整性，不直接替换本地 Schema。

### `list_tables`

输入字段：

- `catalog: str | None`
- `schema_name: str | None`
- `limit: int`，默认 100，最大值由策略限制
- `cursor: str | None`

输出字段：

- `tables: list[TableReference]`
- `next_cursor: str | None`
- `truncated: bool`

`TableReference` 至少包含 `name`，并可包含 `catalog`、`schema_name`。

### `describe_table`

输入字段：

- `table_name: str`
- `schema_name: str | None`

输出字段：

- `table: TableReference`
- `columns: list[ColumnDescription]`
- `primary_key: list[str]`
- `truncated: bool`

`ColumnDescription` 至少包含 `name`、`data_type`、`nullable` 和 `ordinal_position`。

### `run_readonly_sql`

输入字段：

- `sql: str`
- `parameters: dict[str, JsonValue]`
- `max_rows: int`，由策略上限约束

输出字段：

- `columns: list[str]`
- `rows: list[dict[str, JsonValue]]`
- `row_count: int`
- `truncated: bool`

MCP Client 收到的调用参数必须包含策略计算后的 `max_rows`、`timeout_seconds` 和 `read_only=true`。用户输入不能覆盖这些值。

### `get_business_metric`

输入字段：

- `metric_name: str`
- `filters: dict[str, JsonValue]`
- `period: str | None`

输出字段：

- `metric_name: str`
- `value: float`
- `unit: str | None`
- `as_of: datetime | None`
- `dimensions: dict[str, JsonValue]`

指标值必须是有限 JSON 数字；过滤条件和维度数量受策略限制。

## 安全策略

### 权限

四个工具使用以下独立权限：

| 工具 | `required_permissions` |
| --- | --- |
| `list_tables` | `mcp:catalog:read` |
| `describe_table` | `mcp:catalog:read` |
| `run_readonly_sql` | `mcp:query:read` |
| `get_business_metric` | `mcp:metrics:read` |

`ToolExecutor` 在调用处理器前校验权限和 `network_allowed`。权限失败不触达 MCP Client。调用人由 `ToolContext.user_id` 传入 Client；持久化工具调用通过 `task_id` 关联任务所有者，满足调用人追踪而无需新增表。

### 只读 SQL

本地策略验证器在传输前执行以下检查：

1. 只接受单条查询，允许首关键字为 `SELECT` 或 `WITH`。
2. 拒绝字符串字面量和标识符之外的多余分号，拒绝 SQL 注释，避免隐藏第二条语句。
3. 拒绝 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`GRANT`、`REVOKE`、`CALL`、`EXEC` 等写入、DDL、DCL 和过程调用关键字。
4. 使用命名占位符 `:name` 传递变量值；每个占位符必须在 `parameters` 中存在，未使用的参数也拒绝，避免调用者误以为参数已生效。
5. 参数值必须是有限、JSON-safe 的标量或受限容器，不接受 SQL 片段、可执行对象或无限制大对象。
6. 策略把 `max_rows` 限制到最大返回行数；远端响应超过限制时本地截断并设置 `truncated=true`。

静态查询可以没有参数；一旦包含变量值，变量必须使用独立参数映射，不能通过字符串拼接传递。

### 时间与返回边界

`MCPReadOnlyPolicy` 是不可变配置，至少包含：

- `max_query_seconds`
- `max_rows`
- `max_tables`
- `max_columns`
- `max_filter_items`
- `max_parameter_items`

所有值必须为正数或明确的非负上限。工具定义的 `max_runtime_seconds`、策略的 `max_query_seconds` 和 Client 的 `timeout_seconds` 同时生效，取最严格的有效限制。

### 审计

成功、权限拒绝、输入拒绝、超时、依赖失败和输出失败都走现有 `ToolCallRecorder`。审计保留：

- `task_id`、工具名、调用状态、错误码和耗时；
- 通过任务所有者关联的调用人；
- `run_readonly_sql` 的 SQL 文本；
- 参数名和数量，不保留参数值；
- 表名、指标名和结果数量等受限标量。

工具审计快照需要将通用 `parameters` 映射转换为参数名/数量摘要，不能仅依赖调用方自觉脱敏。任何原始异常、连接 URL、Token、密码和路径都不能进入结果或错误消息。

## Agent 集成

M22 不改变默认 `DataAnalysisAgent` 的构造行为，以保持旧调用兼容。启用 MCP 的调用方通过现有 `build_builtin_registry(..., mcp_client=..., mcp_policy=...)` 或等价的 Provider 注册入口创建工具 Registry，再把 Registry/Executor 注入 Agent。

`AgentOrchestrator.STAGE_ALLOWED_TOOLS` 增加：

- 探索阶段：`list_tables`、`describe_table`；
- 分析阶段：`run_readonly_sql`、`get_business_metric`；
- 验证阶段：`describe_table`、`get_business_metric`。

工具是否实际可用仍由 Provider 和 Executor 决定；阶段白名单不能被远端目录动态扩大。

## 错误映射与降级

| 情况 | 结果 |
| --- | --- |
| 工具未注册 | `UNKNOWN_TOOL` |
| 缺少本地权限 | `TOOL_PERMISSION_DENIED` |
| 网络策略关闭 | `TOOL_NETWORK_DENIED` |
| SQL/参数不符合策略 | `TOOL_INPUT_INVALID` |
| Client 超时 | `TOOL_TIMEOUT` |
| MCP 不可用、远端认证失败、协议错误、工具未声明 | `TOOL_DEPENDENCY_FAILED` |
| 远端结果不符合本地 Schema | `TOOL_OUTPUT_INVALID` |
| 审计持久化失败 | 不改变工具成功/失败结果，写入安全日志 |

依赖失败只返回稳定安全消息。Agent 不接收外部异常文本、不把依赖失败转换为空结果，也不未经授权改用另一个数据源。上层阶段可以根据 `TOOL_DEPENDENCY_FAILED` 做有限重试或将阶段标记为不可用；首版不自动重试远端调用。

## 文件边界

预期新增或修改文件：

- `src/data_analysis_agent/mcp/__init__.py`：公开 MCP 端口、模型、策略和 Provider。
- `src/data_analysis_agent/mcp/models.py`：发现描述、调用错误和四个工具的输入/输出模型。
- `src/data_analysis_agent/mcp/client.py`：`MCPClient` Protocol 和稳定异常类型。
- `src/data_analysis_agent/mcp/policy.py`：只读 SQL 验证、参数验证、查询/返回边界策略。
- `src/data_analysis_agent/mcp/provider.py`：工具发现、白名单过滤、注册和处理器适配。
- `src/data_analysis_agent/tools/builtins.py`：为现有 Registry 工厂增加可选 MCP Provider 接入点。
- `src/data_analysis_agent/tools/audit.py`：对 `parameters` 做参数名/数量摘要。
- `src/data_analysis_agent/agent/orchestrator.py`：增加阶段级 MCP 工具白名单。
- `tests/mcp/test_models.py`：输入/输出 Schema 和 JSON-safe 校验。
- `tests/mcp/test_policy.py`：只读 SQL、参数、数量和时间策略。
- `tests/mcp/test_provider.py`：发现、注册、权限、调用和依赖失败。
- `tests/mcp/test_integration.py`：Agent/Orchestrator 阶段白名单和审计追踪。

不新增真实传输或数据库文件；Fake Client 只存在于测试中。

## 验收矩阵

| 验收标准 | 验证方式 |
| --- | --- |
| Agent 可以发现 MCP 工具 | Fake Client 目录经过 Provider 后，Registry 暴露四个静态 Schema；未知工具不出现 |
| 输入和输出有 Schema | 四个输入/输出 Pydantic 模型 JSON Schema 测试 |
| 未授权工具无法调用 | 缺少对应权限时 Executor 返回权限错误，Fake Client 调用次数为零 |
| 只读 SQL 能被限制 | SQL 策略测试写入、多语句、参数绑定、超时和返回行数 |
| 调用记录可追踪 | Recorder 测试 task/user 关联、工具、SQL、参数摘要、状态和耗时 |
| 外部系统不可用有降级 | Fake Client 抛出依赖异常时返回 `TOOL_DEPENDENCY_FAILED` 且不泄露原始异常 |

## 兼容性

- 默认不配置 MCP 时，现有 Agent、Registry、Executor 和旧工具行为不变。
- 新增参数采用可选、关键字参数，不能改变旧调用位置语义。
- M22 首版只依赖现有 Python、Pydantic 和 pytest 能力，不增加 MCP SDK 或网络库依赖。
