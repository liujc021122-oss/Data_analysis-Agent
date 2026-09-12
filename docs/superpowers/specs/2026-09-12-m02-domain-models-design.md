# M02 领域模型与任务状态设计

## 目标

在 M01 建立的 `src/data_analysis_agent/` 包结构上，新增一套可独立复用的 Pydantic 领域模型和任务状态契约，把当前 Agent、执行器和报告流程中散落的核心数据形状固定下来。M02 第一阶段只提供模型、状态转换、错误类型、持久化映射和 API DTO，不改现有 Agent 的运行编排，也不删除 M00 固定的字典返回值。

## 范围与非目标

### 本阶段包含

- 定义 `Dataset`、`AnalysisTask`、`TaskEvent`、`ToolCall`、`ExecutionResult`、`MetricArtifact`、`ChartArtifact`、`ReportArtifact` 和 `AgentState`。
- 定义唯一的 `TaskStatus` 枚举和合法状态转换函数。
- 定义领域错误类型，尤其是非法状态转换错误。
- 定义结构化任务事件，能够记录状态变化、工具调用、执行结果、产物和错误。
- 定义持久化模型与领域模型之间的显式映射。
- 定义 API 请求/响应 DTO，并让 API DTO 使用同一个 `TaskStatus` 枚举。
- 为类型校验、JSON 序列化、状态转换和三层模型边界编写离线测试。

### 本阶段不包含

- 不修改 `DataAnalysisAgent.analyze()`、`CodeExecutor`、报告生成器或 `quick_analysis` 的运行行为。
- 不实现 Worker、HTTP 路由、数据库连接、迁移脚本、队列或外部服务调用；当前仓库还没有这些运行组件。
- 不把 M00 的字典结果直接替换为 Pydantic 对象；后续适配阶段再通过边界转换逐步接入。
- 不在根目录兼容模块中增加第二套模型或状态定义。

## 总体架构

新增三个边界清晰的包：

```text
src/data_analysis_agent/
├── domain/
│   ├── __init__.py
│   ├── enums.py
│   ├── errors.py
│   ├── models.py
│   └── state.py
├── persistence/
│   ├── __init__.py
│   ├── models.py
│   └── mappers.py
└── api/
    ├── __init__.py
    └── schemas.py
```

依赖方向固定为：

```text
domain  <-  persistence.mappers  <-  repository（未来）
   ^
   └──── api.schemas（DTO 映射）
```

`domain` 不导入 API 或持久化模块；`persistence` 不把数据库行伪装成领域对象；`api` 不直接暴露数据库记录。由于当前没有 ORM，`persistence.models` 是数据库友好的 Pydantic 行/JSON 结构，不声称自己是 SQLAlchemy 模型。

Pydantic 作为明确的运行依赖加入 `pyproject.toml`，版本范围为 `pydantic>=2.0,<3.0`。项目继续使用 M01 的 Python `>=3.10` 约束。

## 核心领域模型

所有领域模型继承同一个只用于校验配置的 `DomainModel`：

- `extra="forbid"`：未知字段直接校验失败，避免把拼写错误静默写入任务状态。
- `frozen=True`：领域快照不原地修改；状态转换返回新对象和事件。
- `validate_assignment=True`：若未来解除冻结或扩展可变边界，赋值仍需经过校验。
- 字符串、布尔值和整数使用严格类型，避免把不合法输入悄悄转换成另一种业务值。
- `UUID`、`datetime`、枚举和集合均由 Pydantic 负责 JSON 序列化。

时间字段统一使用带 UTC 时区的 `datetime`，默认值通过 `datetime.now(timezone.utc)` 工厂生成。标识符默认使用 `uuid4()`。

### Dataset

表示一个可被分析任务引用的数据集或输入文件：

| 字段 | 类型 | 规则 |
|---|---|---|
| `dataset_id` | `UUID` | 必填，默认 `uuid4()` |
| `name` | `str` | 必填，去除首尾空白后不能为空 |
| `source_uri` | `str` | 必填，去除首尾空白后不能为空；可为本地路径或对象存储 URI |
| `content_type` | `str` | 默认 `text/csv` |
| `size_bytes` | `int` | 非负，默认 `0` |
| `checksum` | `str \| None` | 可选，不保存密钥 |
| `created_at` | `datetime` | UTC 时间 |
| `metadata` | `dict[str, Any]` | 默认空字典 |

### AnalysisTask

表示一次用户分析请求及其当前生命周期状态：

| 字段 | 类型 | 规则 |
|---|---|---|
| `task_id` | `UUID` | 必填，默认 `uuid4()` |
| `query` | `str` | 必填，去除首尾空白后不能为空 |
| `dataset_ids` | `tuple[UUID, ...]` | 默认空集合，兼容当前无文件的离线契约 |
| `status` | `TaskStatus` | 默认 `PENDING` |
| `max_rounds` | `int` | 严格正整数，默认 `10` |
| `created_at` | `datetime` | UTC 时间 |
| `updated_at` | `datetime` | UTC 时间 |
| `error_code` | `str \| None` | 失败时可填错误分类 |
| `error_message` | `str \| None` | 失败时可填面向调用方的安全消息 |
| `metadata` | `dict[str, Any]` | 默认空字典 |

模型本身不自动把状态改成 `FAILED` 或 `COMPLETED`；所有状态变化都必须经过 `state.transition_task()`，以便同时生成事件。

### TaskEvent

表示任务时间线中的一个不可变事件：

| 字段 | 类型 | 规则 |
|---|---|---|
| `event_id` | `UUID` | 默认 `uuid4()` |
| `task_id` | `UUID` | 必填 |
| `event_type` | `TaskEventType` | 事件分类 |
| `from_status` | `TaskStatus \| None` | 状态变化前的状态；初始事件可为空 |
| `to_status` | `TaskStatus` | 状态变化后的状态 |
| `message` | `str \| None` | 不放置密钥或原始 provider payload |
| `occurred_at` | `datetime` | UTC 时间 |
| `metadata` | `dict[str, Any]` | 默认空字典 |

状态转换产生的事件固定使用 `STATUS_CHANGED`，同时填写 `from_status` 和 `to_status`。

### ToolCall

记录 Agent 或未来 Worker 调用一个工具的生命周期：

| 字段 | 类型 | 规则 |
|---|---|---|
| `tool_call_id` | `UUID` | 默认 `uuid4()` |
| `task_id` | `UUID` | 必填 |
| `tool_name` | `str` | 必填，非空 |
| `arguments` | `dict[str, Any]` | 默认空字典；由调用方决定是否脱敏 |
| `result` | `dict[str, Any] \| str \| None` | 可选结果摘要 |
| `status` | `ToolCallStatus` | `PENDING`、`RUNNING`、`SUCCEEDED` 或 `FAILED` |
| `started_at` | `datetime \| None` | 可选 UTC 时间 |
| `finished_at` | `datetime \| None` | 可选 UTC 时间 |
| `error_message` | `str \| None` | 安全错误消息 |

### ExecutionResult

保留 M00 执行器的四个核心键，同时补充可选元数据：

| 字段 | 类型 | 规则 |
|---|---|---|
| `success` | `bool` | 严格布尔值 |
| `output` | `str` | 默认空字符串 |
| `error` | `str \| None` | 默认 `None` |
| `variables` | `dict[str, Any]` | 默认空字典 |
| `duration_ms` | `int \| None` | 可选非负整数 |

M00 仍继续接收和返回 `{"success", "output", "error", "variables"}` 字典；该模型只作为新增边界的标准结构。

### MetricArtifact

表示一个可用于报告或后续校验的指标：

`artifact_id: UUID`、`name: str`、`value: float`、`unit: str | None`、`description: str | None`、`source_tool_call_id: UUID | None`、`created_at: datetime` 和 `metadata: dict[str, Any]`。

### ChartArtifact

表示一个图表文件：

`artifact_id: UUID`、`filename: str`、`file_path: str`、`mime_type: str`（默认 `image/png`）、`title: str | None`、`description: str | None`、`source_tool_call_id: UUID | None`、`created_at: datetime` 和 `metadata: dict[str, Any]`。

M00 已有的会话目录越界防护继续由报告模块负责；M02 模型只记录已确认的路径字符串，不自行读取文件系统或扩大路径权限。

### ReportArtifact

表示 Markdown 或 Word 报告：

`artifact_id: UUID`、`format: ReportFormat`（`MARKDOWN` 或 `DOCX`）、`file_path: str`、`title: str | None`、`content_hash: str | None`、`created_at: datetime` 和 `metadata: dict[str, Any]`。

### AgentState

表示 Agent/Worker 可以共享的任务快照：

`task_id: UUID`、`status: TaskStatus`、`current_round: int`（非负，默认 `0`）、`events: tuple[TaskEvent, ...]`、`tool_calls: tuple[ToolCall, ...]`、`execution_results: tuple[ExecutionResult, ...]`、`metric_artifacts: tuple[MetricArtifact, ...]`、`chart_artifacts: tuple[ChartArtifact, ...]`、`report_artifacts: tuple[ReportArtifact, ...]`、`context: dict[str, Any]` 和 `updated_at: datetime`。

`AgentState.status` 使用 `TaskStatus`，不再另造 Agent 专用状态枚举。M02 不把这个快照接入现有 Agent；未来适配器负责把旧 `analysis_results` 和 `conversation_history` 转换为事件/调用记录。

## 状态枚举与合法转换

`TaskStatus` 使用以下字符串值，大小写固定：

```text
PENDING
QUEUED
RUNNING
EXPLORING
CLEANING
ANALYZING
VALIDATING
REPORTING
COMPLETED
FAILED
CANCELLED
```

`TaskEventType` 固定为 `STATUS_CHANGED`、`TOOL_CALLED`、`EXECUTION_COMPLETED`、`ARTIFACT_CREATED` 和 `ERROR`。工具调用状态为 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`；报告格式为 `MARKDOWN`、`DOCX`。

合法状态转换如下：

| 当前状态 | 允许的下一状态 |
|---|---|
| `PENDING` | `QUEUED`, `FAILED`, `CANCELLED` |
| `QUEUED` | `RUNNING`, `FAILED`, `CANCELLED` |
| `RUNNING` | `EXPLORING`, `CLEANING`, `ANALYZING`, `VALIDATING`, `REPORTING`, `FAILED`, `CANCELLED` |
| `EXPLORING` | `CLEANING`, `ANALYZING`, `VALIDATING`, `FAILED`, `CANCELLED` |
| `CLEANING` | `ANALYZING`, `VALIDATING`, `FAILED`, `CANCELLED` |
| `ANALYZING` | `EXPLORING`, `CLEANING`, `VALIDATING`, `FAILED`, `CANCELLED` |
| `VALIDATING` | `ANALYZING`, `REPORTING`, `FAILED`, `CANCELLED` |
| `REPORTING` | `COMPLETED`, `FAILED`, `CANCELLED` |
| `COMPLETED` | 无 |
| `FAILED` | 无 |
| `CANCELLED` | 无 |

状态转换 API 采用纯函数风格：

```python
def can_transition(current: TaskStatus, target: TaskStatus) -> bool: ...

def transition_status(current: TaskStatus, target: TaskStatus) -> TaskStatus: ...

def transition_task(
    task: AnalysisTask,
    target: TaskStatus,
    *,
    message: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[AnalysisTask, TaskEvent]: ...
```

`transition_task()` 在非法转换时抛出 `InvalidStatusTransitionError`，在合法转换时返回新的 `AnalysisTask` 和一个 `STATUS_CHANGED` 事件；原对象不修改。传入未知枚举值由 Pydantic/枚举校验拒绝，不通过隐式降级处理。

## 错误边界

`domain.errors` 定义：

- `DomainError(Exception)`：所有领域层自定义错误的基类。
- `InvalidStatusTransitionError(DomainError)`：携带 `current_status` 和 `target_status`，消息只包含状态名，不包含输入 payload、密钥或 provider 响应。
- `PersistenceMappingError(DomainError)`：持久化记录无法转换为领域模型时使用，消息包含字段/记录类型，不回显敏感值。

字段类型、未知字段和枚举值错误使用 Pydantic `ValidationError`，调用方应读取 `errors()` 中的字段路径和错误类型；不把 Pydantic 异常改写成含糊的字符串。

## 持久化模型与映射

`persistence.models` 定义独立的 `PersistenceModel` 和记录类型：

- `DatasetRecord`
- `AnalysisTaskRecord`
- `TaskEventRecord`
- `ToolCallRecord`
- `ExecutionResultRecord`
- `ArtifactRecord`

记录类型使用数据库友好的列名和 JSON 字段，例如 `output_text`、`error_text`、`arguments_json`、`result_json`、`metadata_json` 和 `artifact_type`；它们不是领域模型的别名，也不继承领域模型。

`persistence.mappers` 提供显式双向转换，至少包括：

```python
def task_to_record(task: AnalysisTask) -> AnalysisTaskRecord: ...
def record_to_task(record: AnalysisTaskRecord) -> AnalysisTask: ...
def event_to_record(event: TaskEvent) -> TaskEventRecord: ...
def record_to_event(record: TaskEventRecord) -> TaskEvent: ...
```

映射过程必须保持 UUID、枚举、时间、错误字段和 JSON 元数据；状态字符串无法转换为 `TaskStatus` 时抛出 `PersistenceMappingError` 或明确的 Pydantic 校验错误。M02 不执行数据库 I/O。

## API DTO

`api.schemas` 定义独立的 `APIModel`（`extra="forbid"`、支持 `from_attributes`）和以下 DTO：

- `AnalysisTaskCreateRequest`：`query`、`dataset_ids`、`max_rounds` 和 `metadata`。
- `AnalysisTaskResponse`：任务标识、查询摘要、数据集标识、`TaskStatus`、时间、错误摘要和产物摘要。
- `TaskEventResponse`：事件标识、任务标识、事件类型、前后状态、消息和时间。
- `ExecutionResultResponse`：`success`、`output`、`error`、`variables` 和可选耗时。
- `ArtifactResponse`：统一产物标识、类型、文件路径/格式、描述和创建时间。
- `ErrorResponse`：`code`、`message` 和 `details`，不携带异常原文或密钥。

API DTO 使用 `from data_analysis_agent.domain.enums import TaskStatus`，不复制枚举。API 请求允许空 `dataset_ids`，以保持当前 M00 的无文件离线调用能力；`query` 非空，`max_rounds` 为严格正整数。DTO 只负责边界校验和 JSON 形状，不调用 Agent、数据库或外部 API。

## 序列化契约

所有模型必须支持：

```python
model.model_dump_json()
```

序列化结果中：

- `UUID` 为标准字符串；
- `datetime` 为带时区的 ISO-8601 字符串；
- 枚举为其固定字符串值；
- tuple 集合序列化为 JSON 数组；
- `Path` 不作为模型字段，路径统一存为字符串；
- `Any` 元数据必须是 JSON 可编码值，测试不得放入任意 Python 对象。

## 兼容与接入策略

M02 的新增模型通过 `data_analysis_agent.domain`、`data_analysis_agent.persistence` 和 `data_analysis_agent.api` 导入。暂不从顶层 `data_analysis_agent` 重新导出全部模型，避免扩大 M01 已固定的公共 API；需要状态定义的 Agent、未来 Worker 和 API 统一从 `domain.enums.TaskStatus` 导入。

现有 `DataAnalysisAgent`、`CodeExecutor`、报告模块、根目录兼容桥和 M00 fixtures 不改。未来适配阶段将采用以下边界：

```text
旧 Agent 字典结果 -> adapter -> ExecutionResult / ToolCall / Artifact
状态变化请求 -> transition_task() -> 新 AnalysisTask + TaskEvent
领域对象 -> API mapper -> API DTO
领域对象 -> persistence mapper -> Record
```

这样可以在不破坏 M00 字典契约的前提下逐步接入状态和事件。

## 测试策略与验收

新增离线测试分层如下：

```text
tests/domain/test_models.py
tests/domain/test_state.py
tests/persistence/test_mappers.py
tests/api/test_schemas.py
```

必须覆盖：

1. 每个建议模型都能构造并通过 `model_dump_json()`；
2. 未知 `TaskStatus`、未知字段、空必填字符串和错误字段类型均产生清晰的 Pydantic 错误路径；
3. `success="yes"`、`max_rounds="many"` 等字符串不会被静默转换；
4. 每条合法状态转换成功并生成正确的前后状态事件；
5. 反向转换、终态转换和未知转换抛出 `InvalidStatusTransitionError`；
6. 领域任务经过 persistence record 往返映射后值不丢失；
7. API DTO 与领域模型不是同一类，但使用同一个 `TaskStatus` 枚举并能序列化；
8. 测试不创建真实 Agent、不调用模型、不访问网络或数据库；
9. M01 的完整 no-key 回归继续通过。

## 设计结论

本设计把“业务含义”“存储形状”和“外部 API 形状”分成三个边界，同时将状态转换集中到一个纯函数模块。第一阶段的新增代码只建立契约，不改变当前运行链路；这为后续 Worker、API 和 Agent 适配提供稳定的共同语言。
