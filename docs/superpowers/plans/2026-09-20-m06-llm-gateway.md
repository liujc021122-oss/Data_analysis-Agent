# M06 LLM Gateway Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将 DeepSeek/OpenAI-compatible 模型调用统一到可测试的 LLM 网关，提供明确错误、有限重试、结构化输出、流式输出和调用指标，同时保留旧 LLMHelper 与 YAML 测试替身的迁移兼容性。

**Architecture:** LLMClient 负责请求策略和结果契约，LLMProvider 只负责供应商协议，OpenAICompatibleProvider 复用 OpenAI SDK 接入 DeepSeek 与其他兼容服务。LLMHelper 作为兼容外观委托给网关；Agent 的真实默认路径使用 Pydantic/JSON Schema 结构化动作，现有 FakeLLM 没有结构化接口时走明确标记的 legacy YAML 路径。

**Tech Stack:** Python 3.10+, Pydantic 2, OpenAI SDK, jsonschema, pytest, pytest-asyncio, asyncio, 现有 Settings/LLMConfig 和 M00-M05 离线测试夹具。

## Global Constraints

- 不调用真实 OpenAI、DeepSeek 或对象存储 API；所有新增网关测试使用注入的 Fake Provider 或 SDK transport double。
- API Key 只从 Settings/LLMConfig 读取，任何异常、日志、指标和测试断言都不得泄露密钥、Authorization Header、完整提示词或完整模型原始响应。
- Provider 不实现重试、业务 Schema 校验或日志脱敏；这些策略只在 LLMClient 中实现。
- 网络超时、连接失败、限流和明确的临时 5xx 才能重试；认证错误、参数错误、上下文过长、模型不存在和 Schema 校验失败不能进入传输重试。
- 默认单次请求超时为 60 秒，默认最多 3 次总尝试；退避和 sleep 依赖必须可注入，测试不得真实等待。
- 空模型响应必须抛出明确的 LLM 异常，不能返回空字符串作为成功结果。
- structured_output 解析 JSON、执行 Pydantic/JSON Schema 校验；第一次失败最多触发一次修正请求，仍失败必须阻止 Agent 执行代码。
- YAML 解析保留在兼容层，不能成为新网关动作执行的授权边界。
- 所有新增公共模型和异常都必须能安全序列化或提供稳定的 code/retryable 字段。
- 保留 utils.llm_helper.LLMHelper、data_analysis_agent.services.llm.LLMHelper 和 quick_analysis 的现有导入/调用兼容性，除非旧行为与 M06 新错误契约直接冲突。
- 每个任务按 RED-GREEN-REFACTOR 执行：先写一个会失败的测试，运行确认失败，再写最小实现，运行确认通过后提交。

---

## Task 1: Define Gateway Models, Errors, and Configuration

**Files:**
- Create: src/data_analysis_agent/llm/__init__.py
- Create: src/data_analysis_agent/llm/models.py
- Create: src/data_analysis_agent/llm/errors.py
- Modify: src/data_analysis_agent/config/llm.py
- Modify: pyproject.toml
- Create: tests/llm/__init__.py
- Create: tests/llm/test_models.py
- Create: tests/llm/test_errors.py
- Modify: tests/config/test_settings_contract.py

**Interfaces:**
- Produces ChatMessage, ChatRequest, ProviderUsage, ProviderResponse, ProviderChunk, LLMCallMetrics, LLMResponse, LLMStreamEvent, StructuredOutputRequest, StructuredOutputResponse.
- Produces LLMError and stable subclasses LLMConfigurationError, LLMNetworkError, LLMTimeoutError, LLMRateLimitError, LLMAuthenticationError, LLMRequestError, LLMProviderError, LLMEmptyResponseError, LLMStructuredOutputError, LLMClosedError.
- Extends LLMConfig with timeout_seconds=60.0, max_attempts=3, backoff_base_seconds=0.25, backoff_max_seconds=8.0, and an immutable model_prices mapping.

- [ ] Step 1: Write the failing model and configuration tests

Create tests/llm/test_models.py with these assertions:

    def test_llm_models_are_typed_and_json_serializable():
        request = ChatRequest(
            messages=(ChatMessage(role="user", content="hello"),),
            model="deepseek-chat",
            max_tokens=32,
        )
        metrics = LLMCallMetrics(
            provider="deepseek",
            model="deepseek-chat",
            attempt_count=1,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_ms=12,
            usage=ProviderUsage(input_tokens=4, output_tokens=3, total_tokens=7),
        )
        response = LLMResponse(
            text="ok",
            provider="deepseek",
            model="deepseek-chat",
            metrics=metrics,
        )
        assert request.model_dump_json()
        assert response.model_dump_json()

    def test_chat_request_rejects_empty_messages_and_invalid_tokens():
        with pytest.raises(ValidationError, match="messages"):
            ChatRequest(messages=(), model="deepseek-chat")
        with pytest.raises(ValidationError, match="max_tokens"):
            ChatRequest(
                messages=(ChatMessage(role="user", content="hello"),),
                model="deepseek-chat",
                max_tokens=0,
            )

Create tests/llm/test_errors.py that constructs LLMAuthenticationError with a secret value and asserts code is authentication_error, retryable is False, and the secret is absent from str(error). Construct LLMConfigurationError("OPENAI_API_KEY is required") and assert the field name and configuration_error code are present.

- [ ] Step 2: Run the new tests and verify the expected RED failure

Run: pytest tests/llm/test_models.py tests/llm/test_errors.py -q

Expected: FAIL during import because data_analysis_agent.llm and its models/errors do not exist.

- [ ] Step 3: Implement the typed contracts and configuration validation

Use Pydantic models with extra="forbid", strict positive limits, timezone-aware metric timestamps, and nonblank provider/model/message fields. Use this public model shape:

    class ChatMessage(BaseModel):
        role: Literal["system", "user", "assistant"]
        content: StrictStr

    class ChatRequest(BaseModel):
        messages: tuple[ChatMessage, ...]
        model: StrictStr | None = None
        temperature: StrictFloat | None = Field(default=None, ge=0, le=2)
        max_tokens: StrictInt | None = Field(default=None, gt=0)
        metadata: dict[str, Any] = Field(default_factory=dict)

    class ProviderUsage(BaseModel):
        input_tokens: StrictInt | None = Field(default=None, ge=0)
        output_tokens: StrictInt | None = Field(default=None, ge=0)
        total_tokens: StrictInt | None = Field(default=None, ge=0)
        estimated: StrictBool = False

    class LLMCallMetrics(BaseModel):
        call_id: UUID = Field(default_factory=uuid4)
        provider: StrictStr
        model: StrictStr
        attempt_count: StrictInt = Field(ge=1)
        started_at: datetime
        finished_at: datetime
        duration_ms: StrictInt = Field(ge=0)
        usage: ProviderUsage = Field(default_factory=ProviderUsage)
        estimated_cost_usd: StrictFloat | None = Field(default=None, ge=0)
        request_id: StrictStr | None = None

    class LLMResponse(BaseModel):
        text: StrictStr
        provider: StrictStr
        model: StrictStr
        finish_reason: StrictStr | None = None
        request_id: StrictStr | None = None
        metrics: LLMCallMetrics

ProviderResponse/ProviderChunk hold normalized provider data. LLMStreamEvent has kind ("chunk" or "completed"), optional text, and optional metrics. StructuredOutputRequest carries a ChatRequest plus response_model: type[BaseModel] | None or json_schema: Mapping[str, Any] | None, with exactly one schema source required. StructuredOutputResponse carries the validated object and metrics.

In LLMConfig, validate nonblank API URL/model, positive timeout/attempt/backoff values, and normalize model_prices into a read-only mapping. Add jsonschema>=4.21,<5.0 to project dependencies; JSON Schema validation is a production requirement.

- [ ] Step 4: Run focused tests and configuration regression tests

Run: pytest tests/llm/test_models.py tests/llm/test_errors.py tests/config/test_settings_contract.py -q

Expected: PASS, with no warning containing an API key or model secret.

- [ ] Step 5: Commit the gateway contracts

    git add src/data_analysis_agent/llm src/data_analysis_agent/config/llm.py pyproject.toml tests/llm tests/config/test_settings_contract.py
    git commit -m "feat: define LLM gateway contracts and configuration"

## Task 2: Implement the Provider Protocol and OpenAI-Compatible Adapter

**Files:**
- Create: src/data_analysis_agent/llm/provider.py
- Create: src/data_analysis_agent/llm/openai_compatible.py
- Create: tests/llm/test_openai_compatible.py
- Modify: src/data_analysis_agent/llm/__init__.py

**Interfaces:**
- Produces LLMProvider Protocol with async chat(request), async stream(request), and async close().
- Produces OpenAICompatibleProvider(config, client_factory: Callable[..., Any] | None = None) with no fallback client and no retry loop; the factory receives keyword arguments api_key, base_url, and timeout.
- Provider methods return normalized ProviderResponse/ProviderChunk and never expose OpenAI SDK response types.

- [ ] Step 1: Write failing provider mapping tests

Create a FakeClient whose chat.completions.create records keyword arguments and returns a normalized-looking object with id, model, choices, and usage. Instantiate OpenAICompatibleProvider with an offline LLMConfig and client_factory=lambda **kwargs: fake_client. Assert that the factory receives api_key, base_url, and timeout keyword arguments; chat maps model, messages, temperature, max_tokens, text, finish_reason, and the three usage counts. Assert that calling close twice closes the client once and does not raise.

- [ ] Step 2: Run provider tests and verify RED

Run: pytest tests/llm/test_openai_compatible.py -q

Expected: FAIL because the Provider Protocol and adapter are not defined.

- [ ] Step 3: Implement the protocol and adapter

Define:

    class LLMProvider(Protocol):
        async def chat(self, request: ChatRequest) -> ProviderResponse: pass
        def stream(self, request: ChatRequest) -> AsyncIterator[ProviderChunk]: pass
        async def close(self) -> None: pass

OpenAICompatibleProvider must validate API key at construction, create AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_seconds) through the injected factory, omit temperature for models containing reasoner or deepseek-r1, and map normal and streaming SDK responses. It must call only client.chat.completions.create; no retry or fallback branch belongs here.

Map usage.prompt_tokens, completion_tokens, total_tokens, id, model, choices[0].message.content, and choices[0].finish_reason into normalized models. A missing content value remains None; LLMClient owns the empty-response decision.

- [ ] Step 4: Run provider tests and compile check

Run: pytest tests/llm/test_openai_compatible.py -q; python -m compileall -q src/data_analysis_agent/llm

Expected: PASS and no compile errors.

- [ ] Step 5: Commit the Provider adapter

    git add src/data_analysis_agent/llm tests/llm/test_openai_compatible.py
    git commit -m "feat: add OpenAI-compatible LLM provider"

## Task 3: Implement Chat Gateway Policy, Error Mapping, Retry, and Metrics

**Files:**
- Create: src/data_analysis_agent/llm/client.py
- Create: tests/llm/test_client_chat.py
- Modify: src/data_analysis_agent/llm/errors.py
- Modify: src/data_analysis_agent/llm/models.py
- Modify: src/data_analysis_agent/llm/__init__.py

**Interfaces:**
- Produces LLMClient(config, provider: LLMProvider | None = None, recorder: CallRecorder | None = None, sleep: Callable[[float], Awaitable[None] | None] | None = None, clock: Callable[[], float] | None = None, jitter: Callable[[], float] | None = None).
- Produces async achat(request) -> LLMResponse and sync chat(request) -> LLMResponse.
- Produces CallRecorder Protocol with record(metrics: LLMCallMetrics) -> None.
- Maps OpenAI-compatible and provider exceptions to stable gateway exception classes.

- [ ] Step 1: Write failing retry, error, metrics, and no-key tests

Create a FakeProvider with a queue of exceptions/responses. Test a TimeoutError, another TimeoutError, then ProviderResponse("ok") and assert provider calls are 3, injected delays are [0.1, 0.2], response.metrics.attempt_count is 3, and duration_ms is nonnegative. Test LLMAuthenticationError and assert one provider call. Construct LLMClient with api_key=None and assert LLMConfigurationError mentions OPENAI_API_KEY and provider calls are zero.

- [ ] Step 2: Run chat tests and verify RED

Run: pytest tests/llm/test_client_chat.py -q

Expected: FAIL because LLMClient and its retry/metrics policy are not implemented.

- [ ] Step 3: Implement async policy and sync bridge

At the beginning of every call validate that the client is open and API Key/Base URL/model are present, then resolve request model override. Record monotonic start time, attempt count, and final metrics. On each attempt wrap asyncio.wait_for(provider.chat(request), timeout=config.timeout_seconds). Normalize asyncio.TimeoutError, TimeoutError, APIConnectionError, APITimeoutError, RateLimitError, AuthenticationError, BadRequestError, and APIStatusError into stable classes. Retry only retryable errors and use injected sleep, jitter, and clock dependencies.

Use these methods:

    class CallRecorder(Protocol):
        def record(self, metrics: LLMCallMetrics) -> None: pass

    class LLMClient:
        async def achat(self, request: ChatRequest) -> LLMResponse: pass
        def chat(self, request: ChatRequest) -> LLMResponse:
            return asyncio.run(self.achat(request))
        async def aclose(self) -> None: pass
        def close(self) -> None:
            asyncio.run(self.aclose())

The implementation calls the recorder exactly once for a started provider call, including final failure. A configuration failure before provider invocation produces no provider-call metric. If content is None or whitespace after a successful response, raise LLMEmptyResponseError after recording metrics. Usage from Provider is preferred; missing usage is estimated through an injectable token estimator and marked estimated=True. Use configured input/output prices to compute cost, otherwise leave cost as None.

- [ ] Step 4: Add route/model override and lifecycle tests, then run all focused tests

Assert ChatRequest(model="alternate-model") reaches the Provider, close() is idempotent, and a call after close raises LLMClosedError. Run:

    pytest tests/llm/test_models.py tests/llm/test_errors.py tests/llm/test_openai_compatible.py tests/llm/test_client_chat.py -q

Expected: PASS.

- [ ] Step 5: Commit the chat gateway

    git add src/data_analysis_agent/llm tests/llm/test_client_chat.py
    git commit -m "feat: add LLM gateway retry and metrics policy"

## Task 4: Add Structured JSON/Schema Output and YAML Compatibility Parsing

**Files:**
- Create: tests/llm/test_structured_output.py
- Modify: src/data_analysis_agent/llm/client.py
- Modify: src/data_analysis_agent/llm/models.py
- Modify: src/data_analysis_agent/llm/__init__.py
- Modify: src/data_analysis_agent/services/llm.py
- Modify: tests/contract/test_agent_contract.py

**Interfaces:**
- Produces async astructured_output(request) -> StructuredOutputResponse and sync structured_output on LLMClient.
- LLMHelper.structured_output delegates to the Gateway and returns the typed result; call delegates to LLMClient.chat and returns only text.
- Retains LLMHelper.parse_yaml_response as parser-only compatibility behavior.

- [ ] Step 1: Write failing structured-output tests

Define a Pydantic Action model with extra="forbid", action: str, and optional code. Use a FakeProvider returning first JSON with a non-string action value and then valid JSON. Assert a valid JSON response returns an Action instance. Assert the second provider request contains a schema correction message. Assert two invalid outputs (first malformed JSON, then malformed JSON again) raise LLMStructuredOutputError and no typed executable value is returned.

- [ ] Step 2: Run structured tests and verify RED

Run: pytest tests/llm/test_structured_output.py -q

Expected: FAIL because structured Gateway methods and correction behavior do not exist.

- [ ] Step 3: Implement JSON parsing, Pydantic/JSON Schema validation, and one correction

Strip one optional fenced block, parse with json.loads, validate Pydantic response models with TypeAdapter/model_validate, and validate raw schema mappings with jsonschema.Draft202012Validator. Reject a request with neither schema source or both sources. On failure, append a user message containing only a stable validation summary and schema JSON, then call the same achat policy once. If correction fails, raise LLMStructuredOutputError with attempts=2 and no raw secret/provider payload.

LLMHelper.call must propagate LLMError instead of printing and returning an empty string. Update the old contract test test_model_call_failure_is_normalized_to_empty_response to assert the concrete gateway error and one provider attempt. Keep existing YAML parser tests as parser-only tests.

- [ ] Step 4: Run focused structured and legacy compatibility tests

Run: pytest tests/llm/test_structured_output.py tests/contract/test_agent_contract.py tests/test_llm_helper.py -q

Expected: PASS; no test asserts that a failed real gateway call returns an empty string.

- [ ] Step 5: Commit structured output and compatibility behavior

    git add src/data_analysis_agent/llm src/data_analysis_agent/services/llm.py tests/llm/test_structured_output.py tests/contract/test_agent_contract.py
    git commit -m "feat: add structured output and explicit LLM failures"

## Task 5: Implement Streaming and Final Usage Events

**Files:**
- Create: tests/llm/test_streaming.py
- Modify: src/data_analysis_agent/llm/client.py
- Modify: src/data_analysis_agent/llm/models.py
- Modify: src/data_analysis_agent/llm/openai_compatible.py

**Interfaces:**
- Produces async astream(request) -> AsyncIterator[LLMStreamEvent] and sync stream(request) -> Iterator[LLMStreamEvent].
- Emits LLMStreamEvent(kind="chunk", text=<non-empty chunk>) in provider order and exactly one kind="completed" event with final metrics.

- [ ] Step 1: Write failing streaming tests

Create a FakeStreamProvider whose stream yields ProviderChunk(text="a") and ProviderChunk(text="b"). Assert astream emits texts in order and ends with a completed event whose metrics duration_ms is nonnegative. Create a provider that yields "a" then raises TimeoutError; assert the first chunk is already delivered and the raised error does not replay the chunk.

- [ ] Step 2: Run streaming tests and verify RED

Run: pytest tests/llm/test_streaming.py -q

Expected: FAIL because astream and normalized streaming chunks do not exist.

- [ ] Step 3: Implement streaming without duplicate delivery

Map SDK streaming deltas into ProviderChunk, yield each nonempty text chunk once, accumulate usage if the provider supplies a final usage chunk, and emit one completion event with LLMCallMetrics. If a retryable error occurs before any chunk, retry using the same policy; once a chunk has been yielded, do not retry the stream because replay can duplicate user-visible output. Record final failure metrics through the same recorder.

- [ ] Step 4: Run streaming, Provider, and chat tests

Run: pytest tests/llm/test_streaming.py tests/llm/test_openai_compatible.py tests/llm/test_client_chat.py -q

Expected: PASS.

- [ ] Step 5: Commit streaming support

    git add src/data_analysis_agent/llm tests/llm/test_streaming.py
    git commit -m "feat: add LLM streaming with completion metrics"

## Task 6: Migrate Agent Calls to the Gateway While Preserving FakeLLM Compatibility

**Files:**
- Create: src/data_analysis_agent/agent/schemas.py
- Create: src/data_analysis_agent/agent/llm_port.py
- Create: tests/llm/test_agent_structured_boundary.py
- Modify: src/data_analysis_agent/agent/core.py
- Modify: src/data_analysis_agent/services/llm.py
- Modify: src/data_analysis_agent/services/__init__.py
- Modify: src/data_analysis_agent/__init__.py
- Modify: tests/contract/test_agent_contract.py
- Modify: tests/integration/test_analysis_flow.py

**Interfaces:**
- Produces AgentAction with action Literal["generate_code", "collect_figures", "analysis_complete"] and action-specific validated fields.
- Produces AgentLLMPort with request_action(prompt, system_prompt) -> AgentAction and request_report(prompt, system_prompt) -> str.
- DataAnalysisAgent accepts optional LLM injection for tests, uses LLMClient/LLMHelper by default, and closes an owned client on completion/error.

- [ ] Step 1: Write failing Agent boundary tests

Create tests/llm/test_agent_structured_boundary.py with a NoCallExecutor and a fake structured LLM that raises LLMStructuredOutputError. Call agent._request_structured_action("prompt", "system"), assert the error propagates and executor.calls is empty. Add a real LLMClient plus Fake Provider returning {"action":"future_action","code":"x=1"}; assert correction failure is surfaced and execute_code remains uncalled.

- [ ] Step 2: Run Agent boundary tests and verify RED

Run: pytest tests/llm/test_agent_structured_boundary.py -q

Expected: FAIL because Agent action schemas and _request_structured_action do not exist.

- [ ] Step 3: Implement typed Agent actions and migration port

Use Pydantic Literal and extra="forbid"; validate action-specific requirements before executor access:

    class AgentAction(BaseModel):
        model_config = ConfigDict(extra="forbid")
        action: Literal["generate_code", "collect_figures", "analysis_complete"]
        code: str | None = None
        figures_to_collect: list[FigureRequest] = Field(default_factory=list)
        final_report: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self):
        if self.action == "generate_code" and not (self.code and self.code.strip()):
            raise ValueError("generate_code requires nonblank code")
        if self.action == "analysis_complete" and not self.final_report:
            raise ValueError("analysis_complete requires final_report")
        return self

AgentLLMPort calls LLMHelper.structured_output when the injected helper has it. The production helper passes AgentAction to the Gateway. If the injected object only exposes existing call and parse_yaml_response methods, the port uses the legacy YAML parser and marks that branch as compatibility-only. This keeps current FakeLLM tests deterministic without allowing a real Gateway response to bypass Schema validation.

Replace the analysis loop production self.llm.call path with _request_structured_action. Convert the typed action into the existing internal result dictionaries only after validation. _handle_generate_code receives AgentAction.code, never a raw unvalidated mapping. Preserve execution feedback, chart collection, max-round behavior, and report file behavior. Final report generation uses the same typed path when available and keeps YAML parsing only for legacy FakeLLM.

- [ ] Step 4: Run focused Agent regressions

Run: pytest tests/llm/test_agent_structured_boundary.py tests/contract/test_agent_contract.py tests/integration/test_analysis_flow.py -q

Expected: PASS. Existing FakeLLM YAML tests exercise only the explicit compatibility branch; malformed real structured output never invokes the executor.

- [ ] Step 5: Commit Agent migration

    git add src/data_analysis_agent/agent src/data_analysis_agent/services src/data_analysis_agent/__init__.py tests/llm/test_agent_structured_boundary.py tests/contract/test_agent_contract.py tests/integration/test_analysis_flow.py
    git commit -m "feat: route agent model calls through structured LLM gateway"

## Task 7: Integrate Lifecycle, Exports, and Full Regression Coverage

**Files:**
- Modify: src/data_analysis_agent/agent/core.py
- Modify: src/data_analysis_agent/services/llm.py
- Modify: src/data_analysis_agent/services/__init__.py
- Modify: src/data_analysis_agent/__init__.py
- Modify: tests/test_public_api.py
- Create: tests/llm/test_public_api.py
- Modify: README.md

**Interfaces:**
- Public package exports LLMClient, LLMConfig, gateway errors, request/response models, and OpenAICompatibleProvider without requiring a real API key at import time.
- LLMHelper.close and Agent cleanup are idempotent.
- quick_analysis behavior and parameters remain compatible with M00-M05 contracts.

- [ ] Step 1: Write failing public API and cleanup tests

Create tests/llm/test_public_api.py and assert data_analysis_agent.LLMClient is the canonical llm.LLMClient and data_analysis_agent.LLMConfigurationError is the canonical error. Add an async helper close test that awaits close twice on an injected async close spy without raising.

- [ ] Step 2: Run public API tests and verify RED

Run: pytest tests/llm/test_public_api.py -q

Expected: FAIL because gateway symbols are not exported and cleanup is not normalized.

- [ ] Step 3: Implement exports, lifecycle, and documentation

Export canonical data_analysis_agent.llm types from the llm package and package root. Keep utils/llm_helper.py as a compatibility import. Ensure LLMHelper.close supports the Gateway async close and existing async caller contract without closing the Provider twice. Agent analyze uses finally for an owned LLM client, while injected FakeLLM instances are not forcibly closed unless they expose a compatible close method.

Document offline configuration, LLMClient construction, error classes, and the rule that missing API keys fail on real development/test calls but fail during production settings validation.

- [ ] Step 4: Run complete test and quality suite

Run:
    pytest -q
    python -m compileall -q src
    git diff --check HEAD~7..HEAD
    pip check

Expected: all tests pass, compileall exits 0, diff check reports no whitespace errors, pip check reports no broken requirements. No test may make a network request.

- [ ] Step 5: Review final diff and commit integration

Run:
    git status --short --branch
    git diff --stat codex/m05-storage-abstraction HEAD
    git log --oneline --decorate -12

Then add only M06 gateway/config/Agent compatibility files, tests, dependency metadata, and relevant documentation:

    git add src tests pyproject.toml README.md
    git commit -m "feat: complete M06 LLM gateway integration"

Expected: existing user changes in findings.md, progress.md, and task_plan.md remain untouched.

## Plan Self-Review Checklist

- Spec coverage: Provider abstraction (Task 2), DeepSeek/OpenAI compatibility (Task 2), timeout/retry/error classification (Task 3), model override (Task 3), Token/duration/cost metrics (Task 3), streaming (Task 5), JSON Schema and one correction (Task 4), Agent safety boundary (Task 6), lifecycle/exports (Task 7), and offline tests (all tasks).
- Compatibility coverage: old helper imports, FakeLLM YAML tests, quick_analysis, report generation, and M00-M05 regression suite are retained or explicitly updated where empty-string failure behavior conflicts with M06.
- Completeness scan target: this plan contains no TBD, TODO, FIXME, or unassigned design decisions; each task names concrete files, public methods, test commands, and commit boundaries.
- Type consistency: ChatRequest, ProviderResponse, and LLMResponse are defined in Task 1 and consumed by Provider Task 2 and Gateway Tasks 3-5; AgentAction is defined in Task 6 and consumed only by the Agent port and structured tests.
