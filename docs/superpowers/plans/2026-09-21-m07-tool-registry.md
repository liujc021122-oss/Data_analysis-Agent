# M07 工具注册中心实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 在不破坏 M00-M06 兼容行为的前提下，新增带输入/输出 Schema、权限策略、超时和审计能力的工具注册中心。

**Architecture:** 新增 src/data_analysis_agent/tools/，以不可变工具定义和 Registry 管理工具，以 Executor 统一校验、权限、网络策略和错误，以 AuditRecorder 记录调用结果。内置工具通过依赖注入适配现有数据集、执行器、存储和报告模块；旧 Agent YAML/action 入口保留，只增加可选工具执行桥接。

**Tech Stack:** Python 3.10+、Pydantic 2、asyncio、现有 SQLAlchemy Repository、pytest、Fake handlers；不调用真实模型、数据库、对象存储或网络服务。

## Global Constraints

- 生产代码只位于 src/data_analysis_agent/；新增测试位于 tests/tools/。
- 每项新增生产行为必须先有一个按预期失败的测试；RED 失败原因必须先被观察。
- 工具输入和输出使用 Pydantic 模型；模型输入不接受任意 YAML、原始文件路径或未校验字典。
- 每个请求携带 task_id 和 call_id；未知工具、校验、权限、超时和处理器错误都保留这些标识。
- 审计摘要不保存完整参数、密钥、Authorization、完整路径或异常堆栈。
- run_python_analysis 标记为 HIGH，并要求 execute:python 权限；线程超时不宣称为进程级沙箱。
- 不删除或改变旧 YAML/action 契约；测试不需要 API Key，不访问真实外部服务。
- 每个任务运行聚焦测试；最终运行 pytest -q、compileall、pip check 和 git diff --check。

---

### Task 1: 工具核心模型与错误类型

**Files:**
- Create: tests/tools/__init__.py
- Create: tests/tools/test_models.py
- Create: tests/tools/test_errors.py
- Create: src/data_analysis_agent/tools/__init__.py
- Create: src/data_analysis_agent/tools/models.py
- Create: src/data_analysis_agent/tools/errors.py

**Interfaces:**

- ToolRiskLevel：LOW、HIGH。
- ToolDefinition：name、description、input_model、output_model、side_effect、network_access、max_runtime_seconds、required_permissions、risk_level，以及 to_model_schema()。
- ToolContext：task_id、可选 user_id、permissions、network_allowed、metadata。
- ToolCallRequest：tool_name、task_id、arguments、自动生成 call_id。
- ToolCallResult：call_id、task_id、tool_name、ToolCallStatus、JSON-safe output、error_code、error_message、started_at、finished_at、duration_ms，以及 to_agent_payload()。
- ToolError 子类：UnknownToolError、ToolInputValidationError、ToolOutputValidationError、ToolPermissionError、ToolNetworkDeniedError、ToolContextError、ToolTimeoutError、ToolDependencyError、ToolExecutionError。

- [ ] Step 1: Write the failing model tests

~~~python
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.models import (
    ToolCallRequest, ToolCallResult, ToolContext, ToolDefinition, ToolRiskLevel,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


def definition(name="double_value", runtime=1.5):
    return ToolDefinition(
        name=name, description="Double an integer.", input_model=Input,
        output_model=Output, side_effect=False, network_access=False,
        max_runtime_seconds=runtime, required_permissions=frozenset(),
        risk_level=ToolRiskLevel.LOW,
    )


def test_definition_exports_schema_and_rejects_bad_metadata():
    schema = definition().to_model_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "double_value"
    assert schema["function"]["parameters"]["properties"]["value"]["type"] == "integer"
    with pytest.raises(ValueError, match="name"):
        definition("run python")
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        definition(runtime=0)


def test_request_result_preserve_ids_and_are_json_serializable():
    task_id = uuid4()
    request = ToolCallRequest(task_id=task_id, tool_name="double_value", arguments={"value": 2})
    result = ToolCallResult(
        call_id=request.call_id, task_id=task_id, tool_name=request.tool_name,
        status=ToolCallStatus.SUCCEEDED, output={"doubled": 4},
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00", duration_ms=10,
    )
    payload = result.to_agent_payload()
    assert payload["task_id"] == str(task_id)
    assert payload["call_id"] == str(request.call_id)
    assert payload["success"] is True
    assert result.model_dump_json()


def test_context_requires_task_id_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ToolContext.model_validate({"permissions": [], "network_allowed": False})
    with pytest.raises(ValidationError):
        ToolContext(task_id=uuid4(), unexpected=True)
~~~

- [ ] Step 2: Run the model tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_models.py -q
~~~

Expected: collection fails because data_analysis_agent.tools is not implemented.

- [ ] Step 3: Write the failing error tests

~~~python
from uuid import uuid4

from data_analysis_agent.tools.errors import (
    ToolError, ToolInputValidationError, ToolPermissionError,
    ToolTimeoutError, UnknownToolError,
)


def test_errors_have_stable_codes_and_request_context():
    task_id = uuid4()
    error = ToolPermissionError("run_python_analysis", task_id, "permission denied")
    assert isinstance(error, ToolError)
    assert error.code == "TOOL_PERMISSION_DENIED"
    assert error.tool_name == "run_python_analysis"
    assert error.task_id == task_id
    assert "Authorization" not in str(error)
    assert UnknownToolError("missing", task_id).code == "UNKNOWN_TOOL"
    assert ToolInputValidationError("tool", task_id, "invalid input").code == "TOOL_INPUT_INVALID"
    assert ToolTimeoutError("tool", task_id).code == "TOOL_TIMEOUT"
~~~

- [ ] Step 4: Run the error tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_errors.py -q
~~~

Expected: collection fails because the error module and classes do not exist.

- [ ] Step 5: Implement the minimal models and errors

Implement a frozen Pydantic base with extra="forbid", timezone-aware datetime validation and JSON serialization. Implement ToolDefinition as a frozen dataclass with name regex ^[a-z][a-z0-9_]{0,63}$, positive finite runtime, nonblank description, and BaseModel subclass checks. Its model schema is:

~~~python
{
    "type": "function",
    "function": {
        "name": self.name,
        "description": self.description,
        "parameters": self.input_model.model_json_schema(),
    },
}
~~~

ToolCallResult.to_agent_payload() uses model_dump(mode="json"), adds success = status is ToolCallStatus.SUCCEEDED, and never returns UUID or datetime objects. Error constructors store only fixed code, tool name, task ID and safe detail; they must not include rejected values or tracebacks.

Export public symbols from tools/__init__.py; do not modify the root package yet.

- [ ] Step 6: Run the model and error tests to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_models.py tests/tools/test_errors.py -q
~~~

Expected: all new tests pass.

- [ ] Step 7: Commit the core contract

~~~text
git add src/data_analysis_agent/tools tests/tools
git commit -m "feat: add tool call contracts and errors"
~~~

### Task 2: Registry 与模型 Schema 导出

**Files:**
- Create: tests/tools/test_registry.py
- Create: src/data_analysis_agent/tools/registry.py
- Modify: src/data_analysis_agent/tools/errors.py
- Modify: src/data_analysis_agent/tools/__init__.py

**Interfaces:**

- ToolHandler = Callable[[BaseModel, ToolContext], BaseModel | Mapping[str, Any] | Awaitable[Any]]。
- RegisteredTool contains definition and handler.
- ToolRegistry.register(definition, handler) rejects duplicate names.
- ToolRegistry.get(name, task_id=None) returns RegisteredTool or raises UnknownToolError.
- list_definitions() and model_schemas() return deterministic name-sorted tuples.

- [ ] Step 1: Write the failing Registry tests

~~~python
from uuid import uuid4

import pytest
from pydantic import BaseModel

from data_analysis_agent.tools.errors import UnknownToolError
from data_analysis_agent.tools.models import ToolDefinition, ToolRiskLevel
from data_analysis_agent.tools.registry import ToolRegistry


class EmptyInput(BaseModel):
    pass


class EmptyOutput(BaseModel):
    pass


def definition(name):
    return ToolDefinition(
        name=name, description=name, input_model=EmptyInput, output_model=EmptyOutput,
        side_effect=False, network_access=False, max_runtime_seconds=1,
        required_permissions=frozenset(), risk_level=ToolRiskLevel.LOW,
    )


def test_registry_enforces_unique_names_and_sorted_discovery():
    registry = ToolRegistry()
    handler = lambda value, context: EmptyOutput()
    registry.register(definition("z_tool"), handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition("z_tool"), handler)
    registry.register(definition("a_tool"), handler)
    assert [item.name for item in registry.list_definitions()] == ["a_tool", "z_tool"]
    assert [item["function"]["name"] for item in registry.model_schemas()] == ["a_tool", "z_tool"]


def test_registry_rejects_unknown_tool():
    with pytest.raises(UnknownToolError):
        ToolRegistry().get("not_registered", task_id=uuid4())
~~~

- [ ] Step 2: Run the Registry tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_registry.py -q
~~~

Expected: collection fails because registry.py does not exist.

- [ ] Step 3: Implement the Registry

Store private dict[str, RegisteredTool]; reject non-callable handlers with TypeError and duplicate names with ValueError("tool '<name>' is already registered"). get() passes the caller task ID to UnknownToolError. list_definitions() and model_schemas() sort by name and return copies/tuples so callers cannot mutate internal state.

- [ ] Step 4: Run the Registry tests to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_registry.py -q
~~~

Expected: all Registry tests pass.

- [ ] Step 5: Commit the Registry

~~~text
git add src/data_analysis_agent/tools tests/tools/test_registry.py
git commit -m "feat: add unique tool registry and schemas"
~~~

### Task 3: Executor、策略校验和审计内存记录器

**Files:**
- Create: tests/tools/test_executor.py
- Create: tests/tools/test_audit.py
- Create: src/data_analysis_agent/tools/audit.py
- Create: src/data_analysis_agent/tools/executor.py
- Modify: src/data_analysis_agent/tools/models.py
- Modify: src/data_analysis_agent/tools/errors.py
- Modify: src/data_analysis_agent/tools/__init__.py

**Interfaces:**

- ToolAuditRecord is a frozen JSON-safe model containing request IDs, tool name, status, timestamps, duration, redacted arguments, output summary and safe error fields.
- ToolCallRecorder.record(record: ToolAuditRecord) -> None.
- InMemoryToolCallRecorder.records returns an immutable tuple.
- ToolExecutor(registry, recorder=None) provides execute(request, context) and async aexecute(request, context).
- Policy failures are returned as ToolCallResult(status=FAILED, error_code=...); direct Registry discovery remains exception-based.

- [ ] Step 1: Write the failing Executor and audit tests

~~~python
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.audit import InMemoryToolCallRecorder
from data_analysis_agent.tools.executor import ToolExecutor
from data_analysis_agent.tools.models import (
    ToolCallRequest, ToolContext, ToolDefinition, ToolRiskLevel,
)
from data_analysis_agent.tools.registry import ToolRegistry


class AddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: int
    right: int


class AddOutput(BaseModel):
    total: int


def make_executor(handler, *, runtime=1, permissions=frozenset(), network=False,
                  risk=ToolRiskLevel.LOW, recorder=None):
    definition = ToolDefinition(
        name="add", description="add", input_model=AddInput, output_model=AddOutput,
        side_effect=False, network_access=network, max_runtime_seconds=runtime,
        required_permissions=permissions, risk_level=risk,
    )
    registry = ToolRegistry()
    registry.register(definition, handler)
    return ToolExecutor(registry, recorder=recorder)


def context(task_id, *, permissions=frozenset(), network_allowed=False):
    return ToolContext(task_id=task_id, permissions=permissions,
                       network_allowed=network_allowed)


def test_valid_input_reaches_handler_and_output_is_validated():
    seen = []
    recorder = InMemoryToolCallRecorder()
    task_id = uuid4()
    result = make_executor(
        lambda value, _: (seen.append(value), {"total": value.left + value.right})[1],
        recorder=recorder,
    ).execute(
        ToolCallRequest(task_id=task_id, tool_name="add", arguments={"left": 2, "right": 3}),
        context(task_id),
    )
    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"total": 5}
    assert seen[0].left == 2
    assert recorder.records[0].task_id == task_id


def test_invalid_input_never_enters_handler_and_handler_errors_are_sanitized():
    seen = []
    task_id = uuid4()
    executor = make_executor(lambda value, _: seen.append(value))
    invalid = executor.execute(
        ToolCallRequest(task_id=task_id, tool_name="add",
                        arguments={"left": "bad", "right": 3}),
        context(task_id),
    )
    assert invalid.error_code == "TOOL_INPUT_INVALID"
    assert seen == []

    def raises(value, context):
        raise RuntimeError("secret stack")

    failed = make_executor(raises).execute(
        ToolCallRequest(task_id=task_id, tool_name="add",
                        arguments={"left": 1, "right": 2}),
        context(task_id),
    )
    assert failed.error_code == "TOOL_EXECUTION_FAILED"
    assert "secret stack" not in (failed.error_message or "")


def test_permissions_network_high_risk_and_timeout_are_checked():
    task_id = uuid4()
    no_permission = make_executor(
        lambda value, _: {"total": 1},
        permissions=frozenset({"reports:write"}), risk=ToolRiskLevel.HIGH,
    ).execute(
        ToolCallRequest(task_id=task_id, tool_name="add",
                        arguments={"left": 1, "right": 2}),
        context(task_id),
    )
    assert no_permission.error_code == "TOOL_PERMISSION_DENIED"

    network_denied = make_executor(
        lambda value, _: {"total": 1}, network=True,
    ).execute(
        ToolCallRequest(task_id=task_id, tool_name="add",
                        arguments={"left": 1, "right": 2}),
        context(task_id, network_allowed=False),
    )
    assert network_denied.error_code == "TOOL_NETWORK_DENIED"

    def slow(value, context):
        time.sleep(0.2)
        return {"total": 1}

    timed_out = make_executor(slow, runtime=0.01).execute(
        ToolCallRequest(task_id=task_id, tool_name="add",
                        arguments={"left": 1, "right": 2}),
        context(task_id),
    )
    assert timed_out.status is ToolCallStatus.FAILED
    assert timed_out.error_code == "TOOL_TIMEOUT"


def test_unknown_tool_result_keeps_request_ids():
    task_id = uuid4()
    request = ToolCallRequest(task_id=task_id, tool_name="missing")
    result = ToolExecutor(ToolRegistry()).execute(request, context(task_id))
    assert result.error_code == "UNKNOWN_TOOL"
    assert result.call_id == request.call_id
    assert result.task_id == task_id
~~~

Audit-specific test:

~~~python
from uuid import uuid4

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.audit import InMemoryToolCallRecorder, ToolAuditRecord

def test_memory_recorder_redacts_secret_and_keeps_snapshot():
    recorder = InMemoryToolCallRecorder()
    record = ToolAuditRecord(
        call_id=uuid4(), task_id=uuid4(), tool_name="inspect_dataset",
        status=ToolCallStatus.SUCCEEDED,
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00", duration_ms=10,
        arguments={"dataset_id": "ds-1", "api_key": "do-not-store"},
        output={"row_count": 2},
    )
    recorder.record(record)
    assert recorder.records[0].arguments["api_key"] == "[REDACTED]"
    assert "do-not-store" not in recorder.records[0].model_dump_json()
    assert recorder.records == tuple(recorder.records)
~~~

- [ ] Step 2: Run the Executor and audit tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_executor.py tests/tools/test_audit.py -q
~~~

Expected: collection fails because executor.py and audit.py do not exist.

- [ ] Step 3: Implement audit summaries and execution

Implement recursive redaction: keys containing case-insensitive api_key, authorization, token, password or secret become [REDACTED]; strings cap at 256 characters; mappings keep 20 keys and sequences keep 20 items. ToolAuditRecord is frozen and JSON serializable.

Implement aexecute() with this order: lookup, task ID match, Pydantic input validation, required permissions, network flag, HIGH risk permission execute:python, handler call, output validation, audit. Sync handlers run via asyncio.to_thread; coroutine handlers are awaited; asyncio.wait_for applies max_runtime_seconds. A timeout produces FAILED + TOOL_TIMEOUT; a handler exception produces TOOL_EXECUTION_FAILED without its message or traceback; bad output produces TOOL_OUTPUT_INVALID. execute() calls asyncio.run(aexecute(...)) for normal synchronous callers.

Always build a result with UTC start/end and non-negative integer duration. If context task ID differs from request, return TOOL_CONTEXT_INVALID before the handler. Recorder exceptions are logged with a fixed message and never replace the result.

- [ ] Step 4: Run focused tests to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_executor.py tests/tools/test_audit.py -q
~~~

Expected: all tests pass; timeout returns before the 0.2 second handler completes.

- [ ] Step 5: Commit Executor and in-memory audit

~~~text
git add src/data_analysis_agent/tools tests/tools/test_executor.py tests/tools/test_audit.py
git commit -m "feat: enforce tool execution policies and audit calls"
~~~

### Task 4: 现有持久化 Repository 审计适配

**Files:**
- Create: tests/tools/test_persistence_audit.py
- Modify: src/data_analysis_agent/tools/audit.py
- Modify: src/data_analysis_agent/tools/errors.py
- Modify: src/data_analysis_agent/tools/__init__.py

**Interfaces:**

- RepositoryToolCallRecorder(uow_factory).record(record) writes one existing domain ToolCall and one ExecutionResult in one UnitOfWork.
- ToolAuditError is a stable ToolError with code TOOL_AUDIT_PERSISTENCE_FAILED; Executor retains its result and does not turn a successful tool into a failed tool.

- [ ] Step 1: Write the failing persistence test

Use the existing uow_factory fixture to create a user and AnalysisTask, create a successful ToolAuditRecord, call RepositoryToolCallRecorder.record(), then open a new UnitOfWork and assert:

~~~python
calls = uow.tool_calls.list_for_task(task_id)
assert calls[0].tool_call_id == record.call_id
assert calls[0].tool_name == "inspect_dataset"
assert calls[0].status is ToolCallStatus.SUCCEEDED
executions = uow.executions.list_for_tool_call(record.call_id)
assert executions[0].success is True
assert executions[0].duration_ms == record.duration_ms
~~~

Add a failure test with a uow_factory that raises; the exception must be ToolAuditError and must not contain a database URL or stack.

- [ ] Step 2: Run the persistence test to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_persistence_audit.py -q
~~~

Expected: collection fails because RepositoryToolCallRecorder and ToolAuditError do not exist.

- [ ] Step 3: Implement RepositoryToolCallRecorder

Create ToolCall with audit IDs, task ID, tool name, sanitized arguments, result, status, timestamps and safe error. Create ExecutionResult with success derived from status, JSON text output, output variables and duration. Add both through uow.tool_calls.add() and uow.executions.add(..., tool_call_id=...), then commit. Convert repository/transaction exceptions to ToolAuditError("TOOL_AUDIT_PERSISTENCE_FAILED") and let UnitOfWork roll back.

- [ ] Step 4: Run the persistence test to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_persistence_audit.py -q
~~~

Expected: all persistence tests pass.

- [ ] Step 5: Commit the persistence adapter

~~~text
git add src/data_analysis_agent/tools tests/tools/test_persistence_audit.py
git commit -m "feat: persist tool call audit records"
~~~

### Task 5: 七个内置工具的 I/O 契约和工厂

**Files:**
- Create: tests/tools/test_builtins.py
- Create: src/data_analysis_agent/tools/builtins.py
- Modify: src/data_analysis_agent/tools/errors.py
- Modify: src/data_analysis_agent/tools/__init__.py

**Interfaces:**

- build_builtin_registry(...) always registers inspect_dataset, profile_dataset, run_sql, run_python_analysis, save_chart, validate_metric, generate_report.
- Dataset input is DatasetIdInput(dataset_id: UUID); no source path field.
- SQL input is RunSqlInput(query: StrictStr, parameters: dict[str, Any]); output is RunSqlOutput(rows, row_count).
- Python input is RunPythonAnalysisInput(code: StrictStr); output is existing ExecutionResult.
- Metric input/output require finite values.
- Missing dependencies raise ToolDependencyError only when the affected handler is invoked.

- [ ] Step 1: Write the failing built-in tests

~~~python
from data_analysis_agent.tools.builtins import (
    DatasetIdInput, ValidateMetricInput, ValidateMetricOutput, build_builtin_registry,
)


def test_builtin_registry_has_all_names_and_dataset_input_is_id_only():
    registry = build_builtin_registry()
    assert [item.name for item in registry.list_definitions()] == [
        "generate_report", "inspect_dataset", "profile_dataset",
        "run_python_analysis", "run_sql", "save_chart", "validate_metric",
    ]
    properties = DatasetIdInput.model_json_schema()["properties"]
    assert "dataset_id" in properties
    assert "source_path" not in properties


def test_validate_metric_handler_is_pure_and_normalizes_result():
    registered = build_builtin_registry().get("validate_metric")
    output = registered.handler(
        ValidateMetricInput(name="revenue", value=12.5, unit="CNY"), None
    )
    result = ValidateMetricOutput.model_validate(output)
    assert result.valid is True
    assert result.name == "revenue"
    assert result.value == 12.5
~~~

Add an Executor test with arguments containing dataset_id and source_path; assert TOOL_INPUT_INVALID and no handler call.

- [ ] Step 2: Run built-in tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_builtins.py -q
~~~

Expected: collection fails because tools.builtins does not exist.

- [ ] Step 3: Implement typed models, handlers and definitions

Define strict extra="forbid" models:

~~~python
class DatasetIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID

class RunSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: StrictStr = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

class RunSqlOutput(BaseModel):
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)

class RunPythonAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(min_length=1)

class ValidateMetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1)
    value: StrictFloat
    unit: StrictStr | None = None
    minimum: StrictFloat | None = None
    maximum: StrictFloat | None = None

class ValidateMetricOutput(BaseModel):
    valid: bool
    name: StrictStr
    value: StrictFloat
    unit: StrictStr | None = None
    reason: StrictStr | None = None
~~~

Add typed chart/report input and output models without source_path in model input. Define callback Protocols for SQL, chart saving and report generation.

Implement handlers:

- inspect_dataset: use injected metadata_store and context.user_id; return ID/name/content type/size/checksum/profile summary.
- profile_dataset: call injected DatasetResolver.profile_for_user(dataset_id, owner_id=context.user_id); never open a model-provided path.
- run_sql: call injected sql_runner(query, parameters); normalize rows and count.
- run_python_analysis: call injected CodeExecutor.execute_code(code); validate as ExecutionResult; definition is HIGH and requires execute:python.
- save_chart: pass task ID and typed filename/content/mime type to injected saver; do not construct a Path from model input.
- validate_metric: reject non-finite values and enforce optional minimum/maximum.
- generate_report: call injected report generator with task ID, markdown, format and title; validate artifact metadata.

Register explicit side-effect, network, required permission, risk and runtime metadata for all seven definitions. Dataset tools accept only IDs and use existing user-bound resolver methods.

- [ ] Step 4: Run built-in tests to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_builtins.py tests/tools/test_executor.py -q
~~~

Expected: all built-in and Executor tests pass.

- [ ] Step 5: Commit built-in tools

~~~text
git add src/data_analysis_agent/tools tests/tools/test_builtins.py
git commit -m "feat: register typed built-in analysis tools"
~~~

### Task 6: Agent 可选桥接与公共导出

**Files:**
- Create: tests/tools/test_agent_bridge.py
- Create: tests/tools/test_public_api.py
- Modify: src/data_analysis_agent/agent/core.py
- Modify: src/data_analysis_agent/agent/__init__.py
- Modify: src/data_analysis_agent/__init__.py
- Modify: README.md

**Interfaces:**

Append these constructor arguments after existing llm so old positional calls remain valid:

~~~python
tool_registry: ToolRegistry | None = None
tool_executor: ToolExecutor | None = None
~~~

If only a Registry is supplied, construct ToolExecutor(tool_registry). If both are supplied, require tool_executor.registry is tool_registry. Add:

~~~python
def execute_tool(
    self, tool_name: str, arguments: Mapping[str, Any], *,
    permissions: Iterable[str] = (), network_allowed: bool = False,
) -> ToolCallResult:
    ...
~~~

It creates a request with self.task_id, context using self.dataset_owner_id, delegates to Executor, appends result.to_agent_payload() to conversation_history, and returns the typed result.

- [ ] Step 1: Write the failing bridge/public API tests

~~~python
from pydantic import BaseModel

from data_analysis_agent import DataAnalysisAgent, ToolExecutor, ToolRegistry
from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.models import ToolDefinition, ToolRiskLevel


class Input(BaseModel):
    value: int


class Output(BaseModel):
    result: int


def test_root_exports_tools():
    assert ToolRegistry is not None
    assert ToolExecutor is not None


def test_agent_executes_typed_tool_and_keeps_legacy_default():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="double_value", description="double", input_model=Input,
            output_model=Output, side_effect=False, network_access=False,
            max_runtime_seconds=1, required_permissions=frozenset(),
            risk_level=ToolRiskLevel.LOW,
        ),
        lambda value, context: {"result": value.value * 2},
    )
    agent = DataAnalysisAgent(llm=object(), tool_registry=registry,
                              generate_word_report=False)
    result = agent.execute_tool("double_value", {"value": 4})
    assert result.status is ToolCallStatus.SUCCEEDED
    assert result.output == {"result": 8}
    assert agent.conversation_history[-1]["tool_name"] == "double_value"
    assert DataAnalysisAgent(llm=object(),
                             generate_word_report=False).tool_executor is None
~~~

- [ ] Step 2: Run bridge tests to verify RED

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_agent_bridge.py tests/tools/test_public_api.py -q
~~~

Expected: collection or assertion failure because root exports and execute_tool() do not exist.

- [ ] Step 3: Implement the optional bridge and exports

Import only from canonical data_analysis_agent.tools. Add arguments after llm; expose ToolRegistry, ToolExecutor, tool models, built-in factory and tool errors from the root package. Add a read-only registry property on Executor for the identity check.

Do not modify _request_structured_action(), _process_action(), analyze(), YAML parsing, report generation or LLM lifecycle. The bridge is an adapter and must not make the old path depend on a Registry. Add a short README section with a fake typed handler and explain that the old action contract remains compatible.

- [ ] Step 4: Run bridge tests to verify GREEN

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools/test_agent_bridge.py tests/tools/test_public_api.py -q
~~~

Expected: all bridge/public API tests pass.

- [ ] Step 5: Commit the Agent bridge

~~~text
git add src/data_analysis_agent tests/tools/test_agent_bridge.py tests/tools/test_public_api.py README.md
git commit -m "feat: expose optional tool execution bridge"
~~~

### Task 7: 全量回归、静态检查和验收证据

**Files:**
- Modify: progress.md and findings.md if the ignored planning ledger is used.
- Create: sdd/m07-tool-registry-report.md if the existing SDD workflow requires a report.

- [ ] Step 1: Run the focused M07 suite

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest tests/tools -q
~~~

Expected: all tools tests pass without network or API-key access.

- [ ] Step 2: Run the full empty-key regression

Run in PowerShell:

~~~powershell
$env:OPENAI_API_KEY = ""
$env:OPENAI_BASE_URL = ""
$env:OPENAI_MODEL = ""
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pytest -q
~~~

Expected: M06 baseline and M07 tests pass; no test calls a real model.

- [ ] Step 3: Run compile, dependency and whitespace checks

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m compileall -q src tests
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -m pip check
git diff --check 308dd73..HEAD
~~~

Expected: compileall exits 0, pip check reports no broken requirements, and diff check is empty.

- [ ] Step 4: Verify imports and all seven model schemas offline

Run:

~~~text
E:\桌面\data_analysis_agent-main\data_analysis_agent-main\.venv\Scripts\python.exe -c "from data_analysis_agent import ToolRegistry, ToolExecutor; from data_analysis_agent.tools.builtins import build_builtin_registry; print([item['function']['name'] for item in build_builtin_registry().model_schemas()])"
~~~

Expected:

~~~text
['generate_report', 'inspect_dataset', 'profile_dataset', 'run_python_analysis', 'run_sql', 'save_chart', 'validate_metric']
~~~

- [ ] Step 5: Inspect the final diff against M06

Run:

~~~text
git diff --stat 308dd73..HEAD
git status --short --branch
~~~

Expected: only the tools package, tests, optional Agent bridge, README/API exports and planning/report artifacts are changed; no credential, generated data, image, .env or virtual environment is tracked.

- [ ] Step 6: Commit the final verification ledger

~~~text
git add -f docs/superpowers/plans/2026-09-21-m07-tool-registry.md
git add progress.md findings.md sdd/m07-tool-registry-report.md
git commit -m "docs: record M07 tool registry verification"
~~~

## Review Checkpoints

- After Task 1, review immutable models and stable error codes.
- After Task 3, review timeout semantics and confirm no process-sandbox claim.
- After Task 4, review that complete secrets and paths never enter audit records.
- After Task 5, review each built-in definition’s permissions and dependency boundary.
- Before Task 7 final commit, compare the diff to 308dd73 and run all verification commands fresh.

## Plan Self-Review

- Spec coverage: Registry uniqueness/discovery are Task 2; input/output validation and errors are Tasks 1 and 3; permissions/network/high-risk/runtime are Task 3; task ID/audit are Tasks 3 and 4; built-ins and schemas are Task 5; Agent continuation/public API are Task 6; offline/full regression is Task 7.
- Placeholder scan: no unspecified implementation step is used; persistence tests name the existing uow_factory fixture and exact rows/assertions.
- Type consistency: Executor consumes ToolCallRecorder and produces ToolAuditRecord; persistence maps to existing ToolCall/ExecutionResult; Task 6 consumes the same Registry/Executor types.
- Scope check: no Agent action rewrite, provider-specific function calling, SQL sandbox or process sandbox is included.
