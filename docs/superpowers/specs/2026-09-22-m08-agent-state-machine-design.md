# M08 Agent 状态机设计

## 1. 目标与范围

M08 将当前 `DataAnalysisAgent._analyze_impl()` 中由提示词和 `while` 循环隐式控制的流程，提升为由程序明确控制的 `AgentOrchestrator`。编排器负责任务生命周期、阶段顺序、阶段边界、预算、失败重试、取消、checkpoint 恢复和报告幂等；现有 LLM、代码执行器、存储和报告生成逻辑通过适配器接入，不在本阶段重写其内部实现。

第一版采用渐进式编排壳：编排器先成为稳定的生命周期边界，阶段处理器可以是独立实现，也可以是复用现有 `DataAnalysisAgent` 内部能力的兼容适配器。这样可以逐步迁移旧循环，而不会删除 M00-M07 已固定的 YAML/action、公共 `analyze()` 和 `quick_analysis()` 行为契约。

本阶段不引入 LangGraph，不新增数据库 checkpoint 表，也不要求一次性把所有旧动作改成原生 function calling。checkpoint 由编排器生成并可由调用方保存；后续持久化层可以把它放入任务元数据或专用表，而不改变编排器协议。

## 2. 已确认的设计决策

- 新增 `AgentOrchestrator`，由它驱动固定的分析阶段顺序；不再依赖模型返回的 `continue` 字段决定是否跳过阶段。
- 复用现有 `TaskStatus`、`TaskEvent`、`AgentState` 和 `LEGAL_STATUS_TRANSITIONS` 作为跨 Agent、Worker、API 的核心状态定义；编排器在其上增加更严格的线性阶段策略。
- 阶段处理器通过 Protocol 注入。处理器接收类型化的 `StageInput`，返回类型化的 `StageResult`；原始模型文本、任意 YAML 字典和文件路径不作为编排器接口。
- 保留 `DataAnalysisAgent` 作为兼容外观。旧调用继续获得字典结果；兼容适配器负责将旧的 LLM/action、代码执行和报告方法映射到阶段处理器。
- `ANALYZING` 阶段允许处理器内部多次执行分析步骤，但每次执行都计入步骤预算和模型调用预算；阶段只有在处理器报告完成后才能进入 `VALIDATING`。
- 图表生成不新增状态枚举，作为 `ANALYZING` 阶段内的 `save_chart` 工具调用；报告生成只允许发生在 `REPORTING` 阶段。
- 所有工具调用必须经过现有 `ToolExecutor`，并由编排器按当前阶段的允许工具集合再次校验；未注册工具、当前阶段不允许的工具和权限错误都在进入处理器副作用前失败。
- 阶段失败只重试可重试的处理器失败；取消、非法转换、预算耗尽、输入校验失败和不可恢复的工具/模型错误直接结束任务。连续失败达到上限后进入 `FAILED`，不得继续循环。
- 取消检查发生在每个新阶段、每次阶段重试、每次 LLM 调用和每次工具调用之前。任务进入 `CANCELLED` 后不再启动新的阶段或副作用操作。
- 报告生成使用 checkpoint 中的 `report_generated` 标记和已记录的报告 artifact 双重保护。恢复或重复调用不会再次生成最终报告。

## 3. 分层架构

```text
DataAnalysisAgent / API / Worker
          |
          v
AgentOrchestrator.run(request, checkpoint=None)
  | lifecycle / transition / budget / cancel / checkpoint
  v
StageHandler[TaskStatus]
  | StageInput -> StageResult
  v
LegacyAnalysisAdapter 或独立阶段实现
  | typed LLM port / ToolExecutor / storage / report service
  v
AgentState + TaskEvent + OrchestrationResult
```

### 3.1 编排公开模型

新增 `src/data_analysis_agent/agent/orchestration_models.py`，其中的 Pydantic 模型只包含 JSON-safe 值，并使用现有 `TaskStatus` 与 `AgentState`：

```python
class OrchestratorLimits(BaseModel):
    max_steps: StrictInt = Field(default=50, gt=0)
    max_model_calls: StrictInt = Field(default=20, gt=0)
    max_runtime_seconds: StrictFloat = Field(default=900.0, gt=0)
    max_stage_retries: StrictInt = Field(default=2, ge=0)


class StageInput(BaseModel):
    task: AnalysisTask
    state: AgentState
    stage: TaskStatus
    step_number: StrictInt = Field(ge=0)
    attempt: StrictInt = Field(ge=0)
    allowed_tools: frozenset[StrictStr] = frozenset()
    remaining_model_calls: StrictInt = Field(ge=0)
    context: dict[str, JsonValue] = Field(default_factory=dict)


class StageFailure(BaseModel):
    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)
    retryable: StrictBool = False


class StageResult(BaseModel):
    completed: StrictBool = True
    context_updates: dict[str, JsonValue] = Field(default_factory=dict)
    output: JsonValue | None = None
    model_calls: StrictInt = Field(default=0, ge=0)
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def validate_failure_state(self) -> "StageResult":
        if self.failure is not None and self.completed:
            raise ValueError("failed stage result cannot be marked completed")
        return self


class AgentCheckpoint(BaseModel):
    version: Literal[1] = 1
    task: AnalysisTask
    state: AgentState
    step_number: StrictInt = Field(ge=0)
    stage_attempts: dict[StrictStr, StrictInt] = Field(default_factory=dict)
    report_generated: StrictBool = False
    elapsed_runtime_seconds: StrictFloat = Field(default=0.0, ge=0)
    context: dict[str, JsonValue] = Field(default_factory=dict)
    output: dict[str, JsonValue] = Field(default_factory=dict)


class OrchestrationResult(BaseModel):
    task_id: UUID
    status: TaskStatus
    state: AgentState
    output: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    checkpoint: AgentCheckpoint
```

`StageResult.completed=False` 表示继续当前阶段，不能直接改变任务状态；编排器会在下一步重新调用同一个处理器。`failure` 与 `completed=True` 不能同时表示成功；模型校验器会拒绝该组合，存在 failure 时编排器按失败策略处理并忽略成功输出。所有模型支持 `model_dump(mode="json")`，checkpoint 可直接序列化和恢复，且 `output` 保存已完成阶段的可恢复结果。

`StageHandler` 定义为：

```python
class StageToolCaller(Protocol):
    def __call__(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        ...


class StageHandler(Protocol):
    def __call__(
        self, stage_input: StageInput, call_tool: StageToolCaller
    ) -> StageResult:
        ...
```

处理器不得自行修改 `AgentState`、任务状态或数据库；它只能通过 `StageResult` 返回上下文增量、输出和本次模型调用数。处理器需要工具时只能调用编排器注入的 `StageToolCaller`，不能直接持有或调用 `ToolExecutor`。编排器负责检查当前阶段的 `allowed_tools`、取消标志和工具结果错误，再合并快照、产生事件和进行状态转换。

### 3.2 固定阶段和工具策略

编排器只允许以下主流程：

```text
PENDING -> QUEUED -> RUNNING -> EXPLORING -> CLEANING
         -> ANALYZING -> VALIDATING -> REPORTING -> COMPLETED
```

任何非终态都可以按现有领域规则转入 `FAILED` 或 `CANCELLED`；终态不允许再转换。`ANALYZING` 内的重复步骤是同一状态下的内部迭代，不构造伪造的状态自转换。编排器拒绝从 `EXPLORING` 跳到 `REPORTING`、从 `REPORTING` 回到 `ANALYZING` 等非线性跳转，即使领域层的宽泛矩阵允许该跳转。

阶段允许工具固定为：

| 阶段 | 允许工具 | 说明 |
| --- | --- | --- |
| `RUNNING` | 无 | 初始化会话、执行器和依赖；不执行模型分析副作用 |
| `EXPLORING` | `inspect_dataset`, `profile_dataset` | 检查数据集 ID、权限和初始 profile |
| `CLEANING` | `run_python_analysis` | 执行受限清洗步骤；没有清洗任务时返回空的成功结果 |
| `ANALYZING` | `run_sql`, `run_python_analysis`, `save_chart`, `validate_metric` | 可多轮分析；图表保存属于本阶段 |
| `VALIDATING` | `validate_metric`, `inspect_dataset` | 校验指标和关键结果，不生成报告 |
| `REPORTING` | `generate_report` | 只生成 Markdown/Word 报告；幂等执行 |

`call_tool()` 先检查当前阶段的允许集合，再检查取消标志，最后委托现有 `ToolExecutor`；没有执行器时，任何工具请求都返回稳定的 `ORCHESTRATOR_TOOL_UNAVAILABLE` 失败结果。处理器必须根据 `ToolCallResult.status` 和 `error_code` 选择成功输出或 `StageFailure`，不能把工具失败伪装成阶段成功。

兼容适配器可以在某个阶段不调用工具，但不得绕过编排器提供的 `call_tool()` 边界。数据集仍然只通过 `dataset_id` 访问，模型不得提供或拼接本地文件路径。

### 3.3 `AgentOrchestrator` 生命周期

`src/data_analysis_agent/agent/orchestrator.py` 提供以下接口：

```python
class AgentOrchestrator:
    def __init__(
        self,
        *,
        task: AnalysisTask,
        handlers: Mapping[TaskStatus, StageHandler],
        limits: OrchestratorLimits | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_context_factory: Callable[[UUID], ToolContext] | None = None,
        initial_state: AgentState | None = None,
    ) -> None: ...

    def run(
        self,
        *,
        checkpoint: AgentCheckpoint | None = None,
    ) -> OrchestrationResult: ...

    def cancel(self) -> None: ...

    def checkpoint(self) -> AgentCheckpoint: ...

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolCallResult: ...
```

运行顺序如下：

1. 校验任务、处理器映射和初始状态；如果传入 checkpoint，校验版本、任务 ID、当前状态和上下文 JSON 类型。
2. 将 `PENDING` 自动推进到 `QUEUED` 和 `RUNNING`，每次转换都追加 `STATUS_CHANGED` 事件，并在 `RUNNING` 阶段完成初始化。
3. 对当前阶段执行取消检查、运行时间检查、步骤检查和模型调用预算检查。
4. 创建 `StageInput`，调用该阶段的处理器，并注入 `self.call_tool` 作为 `StageToolCaller`；处理器返回的 `model_calls` 立即累加，不能为负数或超过剩余预算。
5. 使用单调时钟测量本次处理器运行时间，累加到 checkpoint 的 `elapsed_runtime_seconds`；合并 `context_updates`，追加阶段事件，生成新的 checkpoint。`completed=False` 时保留当前状态并进入下一次内部步骤。
6. `completed=True` 时按固定阶段顺序转换到下一阶段。`REPORTING` 完成后将 `report_generated=True`，然后只允许转换到 `COMPLETED`。
7. 返回 `OrchestrationResult`。失败、取消、预算耗尽和异常均返回明确的 `status`、`error_code`、`error_message` 和最后 checkpoint，不把内部堆栈或密钥写入结果。

`run()` 是同步入口；处理器可以在内部适配同步/异步的 LLM 或工具客户端，但编排器本身不引入事件循环，避免破坏现有同步公共 API。后续需要 Worker 时可以在外层调用同一个 checkpoint 协议。

### 3.4 失败、预算和取消

每个阶段单独维护连续失败次数。处理器返回 `StageFailure(retryable=True)` 或抛出已知可重试错误时，若当前次数小于 `max_stage_retries`，记录 `ERROR` 事件并再次调用同一阶段；重试不推进状态，也不重复报告。不可重试失败或达到上限时：

```text
当前活动状态 -> FAILED
```

错误码固定使用 `ORCHESTRATOR_STAGE_FAILED`、`ORCHESTRATOR_MAX_STEPS`、`ORCHESTRATOR_MAX_MODEL_CALLS`、`ORCHESTRATOR_TIMEOUT`、`ORCHESTRATOR_CANCELLED`、`ORCHESTRATOR_INVALID_CHECKPOINT` 等稳定值。模型、工具和执行器的原始错误码保留在事件 metadata 的 `cause_code` 中，并经过现有安全清理函数处理。

达到任一预算后不再调用新的处理器、LLM 或工具。调用前检查剩余预算，调用后检查累计值；阶段返回的 `model_calls` 大于剩余配额时视为预算错误。运行时间使用单调时钟测量当前调用，并把耗时累加到 checkpoint，因此从 checkpoint 恢复时不会重置任务总运行时间；不使用可回拨的墙上时钟。

`cancel()` 只设置线程安全的取消标志；编排器在下一次副作用边界前把任务转到 `CANCELLED`。如果取消发生在处理器返回后，返回结果仍为 `CANCELLED`，并且不会进入下一个阶段。已生成的临时文件由现有存储生命周期清理逻辑负责，编排器不删除非本任务文件。

### 3.5 checkpoint 恢复与报告幂等

checkpoint 在每次状态转换、阶段重试和阶段输出合并后更新。恢复时：

- `PENDING`、`QUEUED` 和活动阶段从 checkpoint 指定位置继续，不重复已完成阶段；
- `COMPLETED` 直接返回已保存结果；
- `FAILED` 和 `CANCELLED` 不自动重试，调用方必须显式创建新的任务或传入允许恢复的活动 checkpoint；
- checkpoint 的 `task.task_id` 必须与编排器任务一致，`checkpoint.task.status` 必须与 `checkpoint.state.status` 一致；版本不支持、状态不在线性阶段、上下文不是 JSON-safe 时立即返回 `ORCHESTRATOR_INVALID_CHECKPOINT`。

`REPORTING` 处理器执行前先检查 `report_generated` 和现有报告 artifact。任一条件表明报告已存在，就跳过 `generate_report`，补齐状态并进入 `COMPLETED`。处理器成功返回后再设置标记，因此报告工具失败不会被误记为已完成。

## 4. 兼容适配器和公共入口

`DataAnalysisAgent` 继续保留现有构造参数、YAML/action 兼容解析和 `analyze()` 返回字典的形状。新增的 `LegacyAnalysisAdapter` 负责：

- 复用现有数据集解析、会话目录、`CodeExecutor` 和敏感列配置；
- 将一次旧 LLM action/代码执行映射为一次 `ANALYZING` 阶段步骤；
- 将 `collect_figures` 映射为 `save_chart` 语义，但仍使用既有安全路径校验和 artifact 存储；
- 将旧的 `analysis_complete` 解释为分析阶段完成信号，而不是直接结束整个任务；
- 在 `REPORTING` 阶段复用 `_generate_final_report()`，并把 Markdown、Word 及 artifact 元数据放入 `OrchestrationResult.output`；
- 将 `OrchestrationResult` 转换回现有 `final_report`、`analysis_results`、`conversation_history`、下载 URL 和报告错误字段。

兼容层不得让旧模型文本直接改变状态，不得把绝对文件路径放入模型提示词。旧 `continue` 字段只作为兼容返回值生成，不参与状态机控制。`quick_analysis()` 的参数和默认行为保持 M01/M04 已修复的契约，并通过兼容外观调用同一编排器。

## 5. 事件和可观测性

所有状态转换使用现有 `transition_task()`/`transition_status()` 的领域校验，并追加 `TaskEvent(event_type=STATUS_CHANGED)`。阶段执行、重试、预算拒绝、工具调用和异常分别使用现有 `TaskEventType`，其中非状态事件的 `to_status` 填当前状态；metadata 至少包括：

```json
{
  "stage": "ANALYZING",
  "step_number": 4,
  "attempt": 1,
  "cause_code": "TOOL_TIMEOUT"
}
```

事件不保存完整 prompt、API key、原始用户文件路径或工具敏感参数。模型调用次数和耗时沿用 `AnalysisTask.model_call_count`、`model_duration_ms` 的持久化字段；本阶段不改变数据库表结构，持久化由现有服务或后续 Worker 适配器负责。

## 6. 测试设计

新增离线测试，使用假的阶段处理器、假的 LLM 和内存工具 Registry，不访问真实模型 API、真实数据库或网络服务：

- `orchestration_models` 的正负校验、JSON 序列化和 checkpoint round-trip；
- 固定阶段顺序、非法跳转拒绝和终态不可继续；
- 每阶段允许工具边界，未注册工具和当前阶段禁用工具在处理器副作用前失败；
- 阶段失败只重试可重试错误，超过 `max_stage_retries` 后进入 `FAILED` 且不再调用处理器；
- 最大步骤数、模型调用数和运行时间达到上限时不启动新步骤；
- 取消后不执行新阶段、不生成新报告；
- 从 `EXPLORING`、`ANALYZING` 和 `REPORTING` checkpoint 恢复；
- 报告处理器在重复运行、恢复和已有 artifact 三种情况下最多调用一次；
- 兼容 `DataAnalysisAgent.analyze()`、旧 action 结果字段和 `quick_analysis()`；
- M00-M07 全量回归，验证无 API Key 环境和错误模块定位信息。

测试名称应同时包含 `orchestrator` 或 `compatibility` 等模块标识，断言错误码、状态和事件 metadata，以区分 Agent 编排失败、LLM 失败、工具/执行器失败。

## 7. 非目标与后续扩展

- 本阶段不删除旧 YAML 解析器、不强制迁移所有 prompt、不修改工具定义的公共字段。
- 本阶段不引入 LangGraph、分布式 Worker、异步消息队列或数据库 checkpoint 迁移。
- 本阶段不实现进程级 Python 沙箱、SQL 权限系统或强制终止已运行的非协作线程。
- 本阶段不新增 `CHARTING` 状态；图表仍归属于 `ANALYZING`，避免改变 M02 状态枚举契约。
- 后续可把每个阶段替换为独立服务、增加人工审核/暂停节点、持久化 checkpoint 和并行分支，而不改变 `StageInput`、`StageResult`、`AgentCheckpoint` 和 `ToolExecutor` 边界。

## 8. 验收标准映射

| 验收标准 | 设计保证 |
| --- | --- |
| 非法状态转换被拒绝 | 领域状态校验 + 编排器线性阶段策略 |
| 每个任务有明确终态 | `COMPLETED`、`FAILED`、`CANCELLED` 三个终态，所有预算/异常都有终止路径 |
| 失败上限后不无限循环 | 每阶段重试计数、最大步骤数、模型调用数和运行时间上限 |
| 可以从中断位置恢复 | 版本化 `AgentCheckpoint` 和阶段/上下文快照 |
| 取消后不执行新步骤 | 每阶段、重试、LLM 和工具边界的取消检查 |
| 最终报告只生成一次 | `report_generated` 与报告 artifact 双重幂等保护 |
