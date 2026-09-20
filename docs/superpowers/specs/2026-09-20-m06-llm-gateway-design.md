# M06 LLM 网关设计

## 1. 目标与范围

M06 将模型调用从 `DataAnalysisAgent` 和旧的 `LLMHelper` 实现中抽离为统一的 LLM 网关。网关负责 Provider 选择、超时、有限重试、错误分类、结构化输出校验、流式输出和调用指标；Agent 只依赖稳定的应用层接口，不直接依赖 OpenAI SDK 或 Provider 异常。

本阶段支持 DeepSeek 以及所有遵循 OpenAI Chat Completions 协议的服务。DeepSeek 通过其 OpenAI-compatible endpoint 接入，不为单一厂商编写 Agent 分支。旧 YAML 解析保留为兼容能力，新网关路径使用 JSON Schema/Pydantic 校验。

本阶段不引入真实模型测试，不保存完整提示词或模型原始响应，不实现模型训练、向量检索或供应商专属工具调用。

## 2. 已确认的设计决策

- 采用 `Provider + Gateway Policy + Compatibility Adapter` 三层结构。
- `LLMClient` 是新的统一调用入口，提供 `chat()`、`structured_output()`、`stream()` 和 `close()`；异步调用提供对应的 `achat()`、`astructured_output()`、`astream()` 和 `aclose()` 形式，供现有异步 helper 和服务使用。
- `OpenAICompatibleProvider` 负责把统一请求转换为 OpenAI SDK 请求；DeepSeek 只通过配置区分 Provider URL 和模型。
- Provider 不负责重试策略、业务 Schema 校验或日志脱敏；这些策略统一由 Gateway 实现。
- `LLMHelper` 暂时保留为兼容外观。它将成功网关响应转换为旧的字符串返回值，并继续提供 YAML 解析方法，但不再吞掉网关异常或将失败转换为空字符串。
- 没有 API Key 时允许离线构造配置对象，但第一次真实调用必须抛出包含 `OPENAI_API_KEY` 的明确配置错误。
- 每次结构化输出最多进行一次修正请求。修正仍失败时抛出结构化输出错误，调用方不得把结果交给代码执行器。
- 现有 YAML 测试替身和旧入口继续可用；真实默认 Agent 路径使用新的结构化输出协议。兼容路径只用于迁移和旧客户端，不作为新的安全边界。

## 3. 分层架构

```text
Agent / service
    |
    v
LLMClient (Gateway)
    |-- request validation
    |-- timeout and retry policy
    |-- error normalization
    |-- JSON/YAML compatibility parsing
    |-- usage, duration and cost recording
    v
LLMProvider protocol
    |
    v
OpenAICompatibleProvider
    |-- OpenAI
    |-- DeepSeek
    |-- other OpenAI-compatible endpoints
```

### 3.1 Provider 边界

新增 `src/data_analysis_agent/llm/` 作为网关模块，建议拆分为以下职责：

- `models.py`：消息、响应、流事件、Token 用量和调用指标模型。
- `provider.py`：Provider、Provider 响应和流式 chunk 的 Protocol 定义。
- `openai_compatible.py`：基于 `AsyncOpenAI` 的 OpenAI-compatible 实现。
- `errors.py`：稳定的网关错误类型和错误代码。
- `client.py`：重试、超时、解析、Schema 校验、指标记录和 Provider 生命周期。

Provider 接口只表达协议能力，不泄露 OpenAI SDK 类型给上层：

```python
class LLMProvider(Protocol):
    async def chat(self, request: ChatRequest) -> ProviderResponse: ...
    async def stream(
        self, request: ChatRequest
    ) -> AsyncIterator[ProviderChunk]: ...
    async def close(self) -> None: ...
```

`ProviderResponse` 至少包含文本、模型、完成原因、请求 ID 和原始 usage 摘要。Provider 不返回空成功响应；供应商没有内容时由 Gateway 统一转换为 `LLMEmptyResponseError`。

`OpenAICompatibleProvider` 接收 `LLMConfig` 创建客户端，使用 `base_url`、`model` 和 `api_key`。它不把 API Key、认证 Header 或原始响应体写入异常、日志或响应对象。

### 3.2 Gateway 接口

Gateway 对外暴露以下同步接口，并提供等价异步接口：

```python
class LLMClient:
    def chat(self, request: ChatRequest) -> LLMResponse: ...
    def structured_output(
        self, request: StructuredOutputRequest
    ) -> StructuredOutputResponse: ...
    def stream(self, request: ChatRequest) -> Iterator[LLMStreamEvent]: ...
    def close(self) -> None: ...
```

同步接口只负责把异步 Provider 安全地适配到现有同步 Agent；异步接口是 `LLMHelper.async_call()` 和未来异步 Worker 的直接调用入口。业务代码不直接调用 `AsyncOpenAI` 或 `AsyncFallbackOpenAIClient`。

`LLMResponse` 不再用裸字符串表示成功或失败：

- `text`：非空模型文本。
- `metrics`：本次调用指标。
- `finish_reason`、`provider`、`model` 和可选 `request_id`。

兼容适配器只在边界处读取 `response.text` 并返回字符串。错误通过异常传播，使调用方可以区分模型失败、Provider 失败和执行器失败。

## 4. 错误分类、超时和重试

### 4.1 稳定错误类型

网关定义以下异常，所有异常都包含稳定的 `code`、`retryable`、`provider`、`model` 和安全诊断信息：

- `LLMConfigurationError`：缺少 API Key、Base URL、模型或配置值非法。
- `LLMNetworkError`：连接失败、DNS、连接重置等网络错误。
- `LLMTimeoutError`：单次调用超过超时限制。
- `LLMRateLimitError`：供应商限流，保留可用的 retry-after 信息。
- `LLMAuthenticationError`：401/403 或等价认证失败。
- `LLMRequestError`：请求参数、上下文长度、模型不存在等不可重试的请求错误。
- `LLMProviderError`：其他供应商错误；只有明确标记为临时的 5xx 错误可重试。
- `LLMEmptyResponseError`：供应商返回成功但没有可用文本。
- `LLMStructuredOutputError`：JSON 解析、Schema 校验或有限修正失败。
- `LLMClosedError`：客户端已关闭后继续调用。

错误消息只保留安全的错误类别、HTTP 状态和脱敏后的摘要，不暴露 API Key、完整提示词、Authorization Header 或供应商原始密钥字段。

### 4.2 重试策略

每次调用有独立的超时预算。默认配置为单次 60 秒、最多 3 次总尝试；实际值由 `LLMConfig` 控制。可重试错误使用指数退避：

```text
delay = min(backoff_max, backoff_base * 2 ** retry_index) + jitter
```

服务端提供 `Retry-After` 时优先使用其值，但仍受最大退避上限约束。测试通过注入 sleep/clock 依赖验证尝试次数和延迟，不进行真实等待。

以下情况不重试：

- 认证失败或 API Key 缺失；
- 请求格式错误、上下文过长、模型不存在；
- Schema 校验失败本身（它只触发一次明确的修正请求，不触发传输重试）；
- 客户端已关闭。

超过最大尝试次数后抛出保留原始分类的网关异常，不返回空字符串，也不静默切换到未配置的备用 Provider。未来的多模型路由只能通过显式配置启用。

## 5. JSON Schema、YAML 兼容和 Agent 安全边界

### 5.1 结构化输出流程

```text
Provider text
    -> strip optional markdown code fence
    -> JSON parse
    -> Pydantic model / JSON Schema validation
    -> valid typed result
```

`structured_output()` 接受一个 Pydantic 模型类型或 JSON Schema，并返回校验后的对象。解析失败和字段类型错误都转换为 `LLMStructuredOutputError`，错误中包含字段定位和可安全展示的校验摘要。

第一次失败后，Gateway 生成新的修正请求，只包含结构化校验错误和原输出的安全摘要，不把完整异常对象或密钥放入提示词。修正请求最多一次；若仍失败，异常直接传播。

Agent 使用一个有限动作 Schema，动作只允许 `generate_code`、`collect_figures` 和 `analysis_complete`。未知动作、缺少代码、错误字段类型或非法图表元数据都在执行前被拒绝。执行器只能接收已通过 Schema 校验的代码字段。

### 5.2 YAML 兼容路径

`LLMHelper.parse_yaml_response()` 保留 fenced YAML 和普通 YAML 的解析能力，用于旧客户端和现有离线测试。它可以返回解析后的 Mapping，但不代表该 Mapping 已通过 Agent 动作 Schema 校验。

新默认路径不以 YAML 解析结果直接驱动执行。旧 helper 路径继续支持现有调用方的迁移行为，但会明确标记为 legacy；一旦使用 Gateway 结构化接口，非法动作不能回退为 `generate_code`。

## 6. 模型切换和配置

`src/data_analysis_agent/config/llm.py` 保留现有 `LLMConfig`，并增加类型化的 Gateway 参数：

- `provider`、`api_key`、`base_url`、`model`；
- `timeout_seconds`；
- `max_attempts`、`backoff_base_seconds`、`backoff_max_seconds`；
- 可选的按模型输入/输出价格表。

配置仍由 `Settings` 从 `APP_ENV` 对应的 dotenv 文件和进程环境读取，API Key 不写入代码。生产环境的配置错误继续在配置边界给出字段名；开发和测试环境可以创建无 Key 的离线设置，但真实网关调用会在边界报错。

单次请求可以覆盖 `model`，用于在同一 Provider 内切换模型。需要切换 Provider 时使用显式的命名路由：

```text
route name -> provider + base_url + model + credential reference
```

路由只保存配置引用，不在日志和请求对象中暴露凭据。没有配置的模型或路由立即抛出 `LLMConfigurationError`。

## 7. 调用指标和生命周期

### 7.1 指标模型

每次调用生成不可变的 `LLMCallMetrics`：

- `call_id`、`provider`、`model`；
- `attempt_count`、`started_at`、`finished_at`、`duration_ms`；
- `input_tokens`、`output_tokens`、`total_tokens`；
- `usage_estimated`，用于区分 Provider usage 和本地估算；
- `estimated_cost_usd`；
- 可选的 Provider `request_id`。

Provider 返回 usage 时优先使用真实输入/输出 Token。没有 usage 时使用可替换的估算器，并将 `usage_estimated=True`；估算失败不会阻断模型结果，只将对应 Token 和成本标记为空。

成本按配置的模型价格计算，价格缺失时 `estimated_cost_usd=None`，不伪造零成本。指标不包含完整 prompt、completion 或密钥。

### 7.2 记录器和持久化边界

Gateway 接受可选的 `CallRecorder` Protocol：

```python
class CallRecorder(Protocol):
    def record(self, metrics: LLMCallMetrics) -> None: ...
```

默认记录器只写脱敏结构化日志。Agent/Worker 可以注入任务级记录器，累计现有任务模型中的调用次数和总耗时，并保存每次 Token/成本明细到事件 metadata 或专用记录器；Gateway 不直接依赖数据库 Session。

调用结果和指标在异常场景也会被记录：认证失败记录一次尝试，重试后的最终失败记录总尝试数和耗时，未开始请求的配置失败不生成 Provider 调用指标。

`close()`/`aclose()` 具有幂等性。关闭后任何调用都抛出 `LLMClosedError`。Agent 在任务结束和异常清理路径中关闭 Gateway；兼容 helper 的 `close()` 转发到 Gateway，不直接管理 Provider。

## 8. Agent 和兼容入口迁移

### 8.1 Agent 依赖

`DataAnalysisAgent` 只依赖应用层 LLM Port。默认构造路径通过配置工厂创建 `LLMClient`；测试可以注入 Fake Provider 或现有 FakeLLM，不需要 OpenAI SDK。

分析循环和最终报告生成都使用同一个 Gateway 实例，因此两类调用共享超时、重试、指标和关闭生命周期。Agent 不捕获并重写 Provider 原始异常，只把稳定的网关错误类别转换为用户可见反馈。

### 8.2 `LLMHelper` 兼容层

保留 `utils/llm_helper.py` 和 `services.llm.LLMHelper` 的导入路径，避免现有外部调用立即中断：

- `call()`/`async_call()` 委托给 Gateway；
- 成功时返回旧字符串格式；
- 失败时保留网关异常类别，不返回 `""`；
- `parse_yaml_response()` 只负责兼容解析，不负责执行授权；
- `close()` 只转发，不重复创建或关闭客户端。

原 `AsyncFallbackOpenAIClient` 的 Provider 级备用逻辑不再作为默认 Gateway 行为。若未来需要多 Provider fallback，必须通过显式路由和同一套重试/指标策略实现，避免当前的隐式切换掩盖认证或模型错误。

## 9. 测试设计

### 9.1 Provider 单元测试

- OpenAI-compatible 请求正确传递 system/user messages、模型、temperature 和 max tokens。
- DeepSeek 只通过配置接入，不触发厂商专属分支。
- Provider 将 SDK 的网络、超时、限流、认证、请求和 5xx 异常交给 Gateway 可识别的分类。
- Provider close 幂等，测试不发出真实网络请求。

### 9.2 Gateway 单元测试

- 缺少 API Key 时抛出 `LLMConfigurationError`，错误包含 `OPENAI_API_KEY` 且不包含密钥值。
- 网络超时和连接错误按指数退避重试，最终保留 `LLMTimeoutError`/`LLMNetworkError`。
- 限流按策略重试；认证错误、参数错误和模型不存在只调用一次。
- 空成功响应抛出 `LLMEmptyResponseError`。
- 单次模型覆盖和显式路由能选择预期 Provider/模型。
- Provider usage 被正确转换；无 usage 时标记为 estimated；耗时和成本计算正确。
- 每次调用都能生成唯一 call ID，成功和失败都调用 recorder。
- stream 片段顺序保持不变，结束事件包含最终指标；中途错误不会重复已经交付的片段。
- close 后调用被拒绝，重复 close 不报错。

### 9.3 结构化输出测试

- 合法 JSON 通过 Pydantic/JSON Schema 并返回类型化对象。
- fenced JSON 可以解析，非法 JSON 能定位为解析错误。
- 字段类型错误、缺失字段和未知动作触发一次修正请求。
- 修正仍失败时抛出 `LLMStructuredOutputError`，不会执行代码。
- YAML 兼容解析器保留现有行为，但 Agent 的 Gateway 路径不会绕过 Schema。

### 9.4 Agent 集成和回归测试

- 正常结构化动作仍能生成并执行代码。
- 执行器失败继续反馈给模型，且能区分执行器错误与模型错误。
- 模型超时/认证/结构化失败的反馈明确标识为 LLM 边界错误。
- Schema 非法、未知动作和空响应均不会调用执行器。
- 最终 Markdown、图表和 Word 报告流程继续通过现有 M00-M05 合约。
- `quick_analysis` 公共入口和旧 `LLMHelper` 导入路径保持兼容。
- 所有测试使用 Fake Provider/FakeLLM、临时存储和离线配置，不需要 API Key，不访问真实模型 API。

## 10. 非目标与后续扩展

- 本阶段不实现供应商专属工具调用、函数调用编排或模型训练。
- 本阶段不隐式在 Provider 之间切换；fallback 需要后续显式路由设计。
- 本阶段不保存完整 prompt/completion 到数据库或日志。
- 本阶段不新增独立的模型调用数据库表；先通过 `LLMCallMetrics`、recorder 和现有任务聚合字段提供可持久化边界。
- 本阶段不要求 Agent 全量异步化；同步 Agent 通过 Gateway 适配器工作，异步 Worker 可使用对应 async 接口。
- 后续可以增加更多 Provider、真正的模型路由、价格配置中心和独立调用审计表，而不改变 Agent 的 LLM Port。
