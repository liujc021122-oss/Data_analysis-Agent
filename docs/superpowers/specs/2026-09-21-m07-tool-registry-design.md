# M07 工具注册中心设计

## 1. 目标与范围

M07 为 Agent 可使用的能力建立统一的工具注册、校验、执行和审计边界。工具不再由模型返回的任意 YAML `action` 字符串直接决定；模型只能选择 Registry 中已注册且有明确输入/输出契约的工具。

本阶段新增 provider-neutral 的 `tools` 包，并保留现有 Agent 的 YAML/action 兼容路径。Agent 不在本阶段重写为完整的工具编排器，但可以通过依赖注入使用同一个 Registry/Executor。所有测试使用假的处理器、假的依赖和离线存储，不访问真实模型、数据库或网络服务。

## 2. 已确认的设计决策

- 采用 `ToolDefinition + ToolRegistry + ToolExecutor + AuditRecorder` 四层结构。
- 输入和输出均使用 Pydantic 模型；处理器只接收经过校验的输入模型和 `ToolContext`，不接收原始模型文本。
- `task_id` 是 `ToolCallRequest` 的必填字段，并在成功、失败、超时和拒绝结果中保留。
- 工具名称在 Registry 内唯一；重复注册和未注册调用都是明确的工具错误。
- Registry 只负责发现和 Schema 导出；权限、网络、高风险和运行时限制集中在 Executor，避免调用方绕过策略。
- 审计记录采用 Protocol；默认提供内存记录器，另提供适配现有 `ToolCallRepository`/`ExecutionRepository` 的持久化记录器。审计只保存脱敏摘要，不保存完整敏感参数。
- 内置工具采用依赖注入工厂。依赖缺失不会导致包导入失败，而是在调用时返回稳定的依赖错误。
- 旧 YAML/action 入口继续保留，M00-M06 的行为契约不被删除；后续模块可以将 Agent action 逐步映射到工具调用。

## 3. 分层架构

```text
Agent / Worker / API adapter
          |
          v
ToolExecutor.execute(request, context)
  | input validation / permission / network / timeout
  v
ToolRegistry -> ToolDefinition -> typed handler
  | output validation / error normalization
  v
ToolCallResult + AuditRecorder
```

### 3.1 工具模型

`src/data_analysis_agent/tools/models.py` 定义以下公开对象：

```python
class ToolRiskLevel(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"

class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effect: bool
    network_access: bool
    max_runtime_seconds: float
    required_permissions: frozenset[str]
    risk_level: ToolRiskLevel

class ToolContext:
    task_id: UUID
    user_id: UUID | None
    permissions: frozenset[str]
    network_allowed: bool
    metadata: Mapping[str, Any]

class ToolCallRequest:
    tool_name: str
    task_id: UUID
    arguments: Mapping[str, Any]
    call_id: UUID

class ToolCallResult:
    call_id: UUID
    task_id: UUID
    tool_name: str
    status: ToolCallStatus
    output: Mapping[str, Any] | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int
```

`ToolDefinition.to_model_schema()` 输出 provider-neutral 的 function schema：工具名、描述和 `input_model.model_json_schema()` 的 parameters。输出 Schema 同时可供 Executor 和 Agent 继续消费。工具名使用小写字母、数字和下划线，长度限制为 1-64 个字符；运行时间必须为正数。

### 3.2 Registry 与 Executor

`ToolRegistry.register(definition, handler)` 对名称建立唯一映射；`get()`、`list()` 和 `model_schemas()` 提供发现能力。`ToolRegistry` 不执行任意字符串或未注册函数。

`ToolExecutor.execute(request, context)` 的固定顺序为：

1. 通过 Registry 查找工具；
2. 用 `input_model.model_validate()` 校验参数，失败时不调用处理器；
3. 校验任务上下文、所需权限、网络开关和高风险权限；
4. 在工具声明的最大运行时间内执行同步或异步处理器；
5. 用 `output_model.model_validate()` 校验处理结果；
6. 将结果转换为 JSON-safe `ToolCallResult` 并交给审计记录器。

同步处理器使用受控 worker future 施加返回截止时间；超时结果立即返回并以现有 `ToolCallStatus.FAILED` 标记，同时使用 `error_code="TOOL_TIMEOUT"`，不会把超时处理器的异常泄露给 Agent。Executor 不承诺强制终止已经运行的原生线程，因此高风险处理器仍必须由调用方注入真正的隔离执行环境。

### 3.3 错误边界

`tools/errors.py` 定义 `ToolError` 及以下稳定错误：`UnknownToolError`、`ToolInputValidationError`、`ToolOutputValidationError`、`ToolPermissionError`、`ToolNetworkDeniedError`、`ToolTimeoutError`、`ToolDependencyError` 和 `ToolExecutionError`。错误包含工具名、任务 ID 和安全错误码，不包含完整输入、文件路径密钥或异常堆栈。处理器抛出的未知异常统一包装为 `ToolExecutionError`。

### 3.4 审计与持久化

`tools/audit.py` 定义 `ToolCallRecorder` Protocol 和 `InMemoryToolCallRecorder`。`RepositoryToolCallRecorder` 在一次调用结束后写入现有领域 `ToolCall`，并将标准化的 `ExecutionResult` 写入现有 Repository；数据库不存在或写入失败时返回审计错误，不改变工具本身已经产生的业务结果。

审计记录包括 `task_id`、`call_id`、工具名、最终状态、开始/结束时间、耗时、错误码和受限参数摘要。字符串和集合摘要限制长度，敏感值按键名脱敏；完整参数永不写入日志。

## 4. 内置工具契约

`tools/builtins.py` 提供 `build_builtin_registry(...)`，注册以下工具及明确的 Pydantic I/O 模型：

- `inspect_dataset`：按 `dataset_id` 返回基本数据集元信息。
- `profile_dataset`：按 `dataset_id` 返回现有 `DatasetProfile`。
- `run_sql`：接收 SQL 和受限参数，返回行列表与行数；网络权限为否。
- `run_python_analysis`：接收分析代码，返回现有 `ExecutionResult`；标记为高风险，要求显式 `execute:python` 权限。
- `save_chart`：接收任务内图表对象信息，通过注入的存储服务保存并返回 artifact 元数据。
- `validate_metric`：接收指标名称、数值、单位和可选约束，返回标准化验证结果；无副作用。
- `generate_report`：接收 Markdown 报告内容和格式，通过注入的报告处理器返回报告 artifact 元数据。

数据集工具只接收 ID，不接受模型提供的原始文件路径；处理器通过现有 `DatasetResolver` 完成用户边界校验。`run_sql`、存储和报告的具体实现均通过 Protocol 注入，避免工具包直接绑定某个数据库或对象存储供应商。

## 5. 测试设计

新增离线测试覆盖：

- 工具定义字段校验、名称唯一性和 JSON Schema 导出；
- 未注册工具拒绝；
- 输入类型/未知字段错误时处理器调用次数为零；
- 输出 Schema 不匹配和处理器异常的统一包装；
- task ID/call ID 在成功、失败、拒绝、超时审计记录中一致；
- 权限、网络和高风险工具边界；
- 超时返回和最大运行时间；
- 内置工具名称、Schema、依赖注入和 ID-only 数据集边界；
- `ToolCallResult.to_agent_payload()` 可直接供 Agent 上下文继续使用；
- M06 全量回归、无 API Key、无真实外部服务。

## 6. 非目标与后续扩展

- 本阶段不删除 YAML 解析器，不强制把现有 Agent action 全量迁移到工具调用。
- 本阶段不实现进程级 Python 沙箱、SQL 权限系统或供应商专属 function-calling；Executor 的高风险标记是策略边界，不替代隔离运行时。
- 本阶段不保存大文件本体，不允许前端直接调用内部处理器；后续 API 层只暴露经过授权的工具/任务服务。
- 后续可以增加异步 Worker、分布式审计、按用户/任务的配额和真正的工具调用编排，而不改变工具定义和 Registry 接口。
