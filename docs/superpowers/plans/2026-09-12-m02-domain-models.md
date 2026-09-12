# M02 领域模型与任务状态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 M01 的 canonical 包结构上新增可复用的 Pydantic 领域模型、任务状态转换、持久化记录模型和 API DTO，同时保持 M00/M01 既有运行行为不变。

**Architecture:** `domain` 是唯一的业务模型和状态定义来源；`persistence` 通过独立的数据库友好记录和显式 mapper 与 domain 互转；`api` 通过独立 DTO 暴露外部请求/响应形状。第一阶段不把这些模型接入 Agent、执行器、报告生成器或真实 API，后续适配器再渐进转换 M00 的字典结果。

**Tech Stack:** Python `>=3.10`、Pydantic `>=2.0,<3.0`、pytest、pytest-asyncio；测试只使用本地数据和内存对象，不访问网络、数据库或真实模型。

## Global Constraints

- 生产实现只能位于 `src/data_analysis_agent/`；根目录兼容模块不能新增第二套业务模型。
- 不修改 `DataAnalysisAgent.analyze()`、`CodeExecutor`、报告生成器、`quick_analysis`、根入口或 M00 fixtures 的运行行为。
- `TaskStatus` 只能包含 `PENDING`、`QUEUED`、`RUNNING`、`EXPLORING`、`CLEANING`、`ANALYZING`、`VALIDATING`、`REPORTING`、`COMPLETED`、`FAILED`、`CANCELLED`。
- 领域模型、持久化模型和 API DTO 是三个不同的类；API、未来 Worker 和 Agent 状态契约共享 `domain.enums.TaskStatus`。
- Pydantic 模型使用 `extra="forbid"`；核心字符串、布尔值和整数使用严格类型；空白必填字符串必须拒绝。
- 所有新增模型必须支持 `model_dump_json()`，UUID、UTC datetime、枚举和集合必须得到 JSON 可编码结果。
- 合法状态转换必须集中在 `domain.state`；非法转换抛出 `InvalidStatusTransitionError`，不能静默降级。
- 持久化层本阶段只实现 Pydantic record 和 mapper，不执行 SQL、ORM、文件或外部服务 I/O。
- API DTO 本阶段只负责边界校验和 JSON 形状，不调用 Agent、Worker、数据库或外部 API。
- 每个实现任务先写并运行针对性失败测试，再写最小实现；每个任务完成后运行相关测试并提交独立 commit。
- 每个任务结束前保留 M00/M01 no-key 契约；最终必须重新运行完整测试集，不能要求 API Key。

---

### Task 1: Pydantic 依赖与领域枚举/错误基座

**Files:**
- Modify: `pyproject.toml` dependency list
- Modify: `requirements.txt`
- Create: `src/data_analysis_agent/domain/__init__.py`
- Create: `src/data_analysis_agent/domain/enums.py`
- Create: `src/data_analysis_agent/domain/errors.py`
- Test: `tests/domain/__init__.py`
- Test: `tests/domain/test_primitives.py`

**Interfaces:**
- Produces `TaskStatus`, `TaskEventType`, `ToolCallStatus` and `ReportFormat` string enums.
- Produces `DomainError`, `InvalidStatusTransitionError` and `PersistenceMappingError`.
- Produces a package namespace that later tasks extend without changing the top-level `data_analysis_agent` public API.

- [ ] **Step 1: Write the failing tests**

Create `tests/domain/__init__.py` as an empty package marker and `tests/domain/test_primitives.py`:

```python
import pytest

from data_analysis_agent.domain.enums import (
    ReportFormat,
    TaskEventType,
    TaskStatus,
    ToolCallStatus,
)
from data_analysis_agent.domain.errors import (
    DomainError,
    InvalidStatusTransitionError,
    PersistenceMappingError,
)


def test_task_status_uses_the_exact_shared_values():
    assert [status.value for status in TaskStatus] == [
        "PENDING",
        "QUEUED",
        "RUNNING",
        "EXPLORING",
        "CLEANING",
        "ANALYZING",
        "VALIDATING",
        "REPORTING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    ]


def test_event_tool_and_report_enums_are_json_friendly_strings():
    assert TaskEventType.STATUS_CHANGED.value == "STATUS_CHANGED"
    assert ToolCallStatus.SUCCEEDED.value == "SUCCEEDED"
    assert ReportFormat.MARKDOWN.value == "MARKDOWN"
    assert isinstance(TaskStatus.PENDING, str)


def test_domain_errors_have_a_stable_safe_hierarchy():
    error = InvalidStatusTransitionError(TaskStatus.COMPLETED, TaskStatus.RUNNING)

    assert isinstance(error, DomainError)
    assert isinstance(error, Exception)
    assert error.current_status is TaskStatus.COMPLETED
    assert error.target_status is TaskStatus.RUNNING
    assert "COMPLETED" in str(error)
    assert "RUNNING" in str(error)
    assert isinstance(PersistenceMappingError("invalid record"), DomainError)


def test_invalid_status_values_are_rejected_by_the_enum():
    with pytest.raises(ValueError):
        TaskStatus("BROKEN")
```

- [ ] **Step 2: Run the focused tests and verify the expected red state**

Run:

```powershell
python -m pytest -q tests/domain/test_primitives.py
```

Expected: collection fails because `data_analysis_agent.domain` and its enums/errors do not exist yet. Do not add a test-only fallback import.

- [ ] **Step 3: Add the explicit Pydantic dependency and implement the primitives**

Add the exact runtime dependency to both dependency declarations:

```toml
"pydantic>=2.0,<3.0",
```

Create `src/data_analysis_agent/domain/enums.py`:

```python
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    EXPLORING = "EXPLORING"
    CLEANING = "CLEANING"
    ANALYZING = "ANALYZING"
    VALIDATING = "VALIDATING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskEventType(str, Enum):
    STATUS_CHANGED = "STATUS_CHANGED"
    TOOL_CALLED = "TOOL_CALLED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ERROR = "ERROR"


class ToolCallStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReportFormat(str, Enum):
    MARKDOWN = "MARKDOWN"
    DOCX = "DOCX"
```

Create `src/data_analysis_agent/domain/errors.py`:

```python
from .enums import TaskStatus


class DomainError(Exception):
    """Base class for safe domain-layer errors."""


class InvalidStatusTransitionError(DomainError):
    def __init__(self, current_status: TaskStatus, target_status: TaskStatus):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            "Illegal task status transition: "
            f"{current_status.value} -> {target_status.value}"
        )


class PersistenceMappingError(DomainError):
    """A storage record cannot be converted into a domain object."""
```

Create the initial `src/data_analysis_agent/domain/__init__.py` with only primitive exports; the model and state exports are added in their own tasks:

```python
from .enums import ReportFormat, TaskEventType, TaskStatus, ToolCallStatus
from .errors import DomainError, InvalidStatusTransitionError, PersistenceMappingError

__all__ = [
    "DomainError",
    "InvalidStatusTransitionError",
    "PersistenceMappingError",
    "ReportFormat",
    "TaskEventType",
    "TaskStatus",
    "ToolCallStatus",
]
```

- [ ] **Step 4: Run the focused tests and dependency checks**

Run:

```powershell
python -m pytest -q tests/domain/test_primitives.py
python -m pip check
```

Expected: all primitive tests pass and pip reports `No broken requirements found.`

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml requirements.txt src/data_analysis_agent/domain tests/domain
git commit -m "feat: add M02 domain status primitives"
```

---

### Task 2: Pydantic 核心领域模型与产物模型

**Files:**
- Create: `src/data_analysis_agent/domain/models.py`
- Modify: `src/data_analysis_agent/domain/__init__.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Produces `Dataset`, `AnalysisTask`, `TaskEvent`, `ToolCall`, `ExecutionResult`, `MetricArtifact`, `ChartArtifact`, `ReportArtifact` and `AgentState`.
- All models are immutable Pydantic snapshots with forbidden extra fields and strict primitive validation.
- `ExecutionResult` retains M00 keys `success`, `output`, `error` and `variables`.

- [ ] **Step 1: Write the failing model tests**

Create `tests/domain/test_models.py`:

```python
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import (
    ReportFormat,
    TaskEventType,
    TaskStatus,
    ToolCallStatus,
)
from data_analysis_agent.domain.models import (
    AgentState,
    AnalysisTask,
    ChartArtifact,
    Dataset,
    ExecutionResult,
    MetricArtifact,
    ReportArtifact,
    TaskEvent,
    ToolCall,
)


def test_all_core_models_construct_and_serialize_to_json():
    task_id = uuid4()
    tool_call_id = uuid4()
    now = datetime.now(timezone.utc)
    task = AnalysisTask(task_id=task_id, query="分析销售数据")

    models = [
        Dataset(name="sales.csv", source_uri="sales.csv"),
        task,
        TaskEvent(
            task_id=task_id,
            event_type=TaskEventType.STATUS_CHANGED,
            from_status=TaskStatus.PENDING,
            to_status=TaskStatus.QUEUED,
            occurred_at=now,
        ),
        ToolCall(
            task_id=task_id,
            tool_name="code_executor",
            status=ToolCallStatus.SUCCEEDED,
            arguments={"code": "print(1)"},
            result={"success": True},
            started_at=now,
            finished_at=now,
        ),
        ExecutionResult(
            success=True,
            output="1",
            error=None,
            variables={"value": 1},
            duration_ms=12,
        ),
        MetricArtifact(
            name="revenue",
            value=12.5,
            unit="CNY",
            source_tool_call_id=tool_call_id,
        ),
        ChartArtifact(
            filename="trend.png",
            file_path="outputs/session/trend.png",
            title="Trend",
        ),
        ReportArtifact(
            format=ReportFormat.MARKDOWN,
            file_path="outputs/session/report.md",
            title="Report",
        ),
        AgentState(task_id=task_id, status=TaskStatus.ANALYZING, current_round=2),
    ]

    for model in models:
        encoded = model.model_dump_json()
        decoded = json.loads(encoded)
        assert isinstance(decoded, dict)
        assert encoded

    assert json.loads(task.model_dump_json())["status"] == "PENDING"
    assert json.loads(models[0].model_dump_json())["created_at"].endswith("+00:00")


def test_analysis_task_defaults_to_pending_and_preserves_m00_execution_keys():
    task = AnalysisTask(query="离线分析")
    result = ExecutionResult(success=False, error="planned failure")

    assert task.status is TaskStatus.PENDING
    assert task.dataset_ids == ()
    assert task.max_rounds == 10
    assert set(result.model_dump()) >= {"success", "output", "error", "variables"}
    assert result.success is False
    assert result.output == ""
    assert result.variables == {}


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: AnalysisTask(query="x", status="BROKEN"), "status"),
        (lambda: AnalysisTask(query="x", max_rounds="10"), "max_rounds"),
        (lambda: ExecutionResult(success="true"), "success"),
        (lambda: Dataset(name=" ", source_uri="file.csv"), "name"),
    ],
)
def test_invalid_domain_values_report_their_field(factory, field):
    with pytest.raises(ValidationError) as exc_info:
        factory()

    assert field in str(exc_info.value)


def test_unknown_fields_are_rejected_instead_of_silently_saved():
    with pytest.raises(ValidationError, match="unexpected"):
        AnalysisTask(query="x", unexpected="value")


def test_domain_snapshots_are_frozen():
    task = AnalysisTask(query="x")

    with pytest.raises(ValidationError):
        task.status = TaskStatus.RUNNING
```

- [ ] **Step 2: Run the model tests and verify the expected red state**

Run:

```powershell
python -m pytest -q tests/domain/test_models.py
```

Expected: collection fails because `data_analysis_agent.domain.models` does not exist yet.

- [ ] **Step 3: Implement the domain models**

Create `src/data_analysis_agent/domain/models.py` with the following exact model contract:

```python
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from .enums import ReportFormat, TaskEventType, TaskStatus, ToolCallStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )


class Dataset(DomainModel):
    dataset_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    source_uri: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(default=0, ge=0)
    checksum: StrictStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_text = field_validator("name", "source_uri", "content_type")(_nonblank)


class AnalysisTask(DomainModel):
    task_id: UUID = Field(default_factory=uuid4)
    query: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)


class TaskEvent(DomainModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: TaskEventType
    from_status: TaskStatus | None = None
    to_status: TaskStatus
    message: StrictStr | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(DomainModel):
    tool_call_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    tool_name: StrictStr
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | StrictStr | None = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: StrictStr | None = None

    _validate_tool_name = field_validator("tool_name")(_nonblank)


class ExecutionResult(DomainModel):
    success: StrictBool
    output: StrictStr = ""
    error: StrictStr | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)


class MetricArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    value: StrictFloat
    unit: StrictStr | None = None
    description: StrictStr | None = None
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_name = field_validator("name")(_nonblank)


class ChartArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    filename: StrictStr
    file_path: StrictStr
    mime_type: StrictStr = "image/png"
    title: StrictStr | None = None
    description: StrictStr | None = None
    source_tool_call_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_filename = field_validator("filename")(_nonblank)
    _validate_file_path = field_validator("file_path")(_nonblank)


class ReportArtifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    format: ReportFormat
    file_path: StrictStr
    title: StrictStr | None = None
    content_hash: StrictStr | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_file_path = field_validator("file_path")(_nonblank)


class AgentState(DomainModel):
    task_id: UUID
    status: TaskStatus = TaskStatus.PENDING
    current_round: StrictInt = Field(default=0, ge=0)
    events: tuple[TaskEvent, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    execution_results: tuple[ExecutionResult, ...] = ()
    metric_artifacts: tuple[MetricArtifact, ...] = ()
    chart_artifacts: tuple[ChartArtifact, ...] = ()
    report_artifacts: tuple[ReportArtifact, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)
```

Append the model imports and names to `src/data_analysis_agent/domain/__init__.py`:

```python
from .models import (
    AgentState,
    AnalysisTask,
    ChartArtifact,
    Dataset,
    DomainModel,
    ExecutionResult,
    MetricArtifact,
    ReportArtifact,
    TaskEvent,
    ToolCall,
)
```

and include all imported names in `__all__`.

- [ ] **Step 4: Run model tests and the existing M00/M01 contract groups**

Run:

```powershell
python -m pytest -q tests/domain/test_models.py tests/domain/test_primitives.py tests/contract tests/integration tests/public_api tests/packaging
```

Expected: all focused model tests and all existing contract/integration tests pass; no test constructs a real OpenAI client.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/domain tests/domain
git commit -m "feat: add M02 domain data models"
```

---

### Task 3: 合法任务状态转换与事件生成

**Files:**
- Create: `src/data_analysis_agent/domain/state.py`
- Modify: `src/data_analysis_agent/domain/__init__.py`
- Test: `tests/domain/test_state.py`

**Interfaces:**
- Produces `LEGAL_STATUS_TRANSITIONS`, `can_transition()`, `transition_status()` and `transition_task()`.
- `transition_task()` returns a new `AnalysisTask` and a `TaskEvent`; it never mutates its input.
- Terminal states `COMPLETED`, `FAILED` and `CANCELLED` have no outgoing transitions.

- [ ] **Step 1: Write the failing transition tests**

Create `tests/domain/test_state.py`:

```python
from datetime import datetime, timezone

import pytest

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.errors import InvalidStatusTransitionError
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.domain.state import (
    LEGAL_STATUS_TRANSITIONS,
    can_transition,
    transition_status,
    transition_task,
)


def test_legal_transition_matrix_contains_the_declared_lifecycle():
    assert LEGAL_STATUS_TRANSITIONS[TaskStatus.PENDING] == frozenset(
        {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    )
    assert LEGAL_STATUS_TRANSITIONS[TaskStatus.REPORTING] == frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    )
    for terminal in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        assert LEGAL_STATUS_TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.EXPLORING),
        (TaskStatus.EXPLORING, TaskStatus.CLEANING),
        (TaskStatus.CLEANING, TaskStatus.ANALYZING),
        (TaskStatus.ANALYZING, TaskStatus.VALIDATING),
        (TaskStatus.VALIDATING, TaskStatus.REPORTING),
        (TaskStatus.REPORTING, TaskStatus.COMPLETED),
    ],
)
def test_declared_lifecycle_transitions_are_allowed(current, target):
    assert can_transition(current, target) is True
    assert transition_status(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
        (TaskStatus.FAILED, TaskStatus.QUEUED),
        (TaskStatus.CANCELLED, TaskStatus.PENDING),
    ],
)
def test_illegal_transitions_raise_a_specific_domain_error(current, target):
    assert can_transition(current, target) is False

    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        transition_status(current, target)

    assert exc_info.value.current_status is current
    assert exc_info.value.target_status is target


def test_transition_task_returns_a_new_task_and_status_event():
    occurred_at = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    task = AnalysisTask(query="分析样例", status=TaskStatus.PENDING)

    updated, event = transition_task(
        task,
        TaskStatus.QUEUED,
        message="queued by test",
        occurred_at=occurred_at,
    )

    assert updated is not task
    assert task.status is TaskStatus.PENDING
    assert updated.status is TaskStatus.QUEUED
    assert updated.updated_at == occurred_at
    assert event.task_id == task.task_id
    assert event.event_type is TaskEventType.STATUS_CHANGED
    assert event.from_status is TaskStatus.PENDING
    assert event.to_status is TaskStatus.QUEUED
    assert event.message == "queued by test"
    assert event.occurred_at == occurred_at
```

- [ ] **Step 2: Run the transition tests and verify the expected red state**

Run:

```powershell
python -m pytest -q tests/domain/test_state.py
```

Expected: collection fails because `data_analysis_agent.domain.state` does not exist yet.

- [ ] **Step 3: Implement the transition matrix and pure functions**

Create `src/data_analysis_agent/domain/state.py`:

```python
from datetime import datetime
from typing import Mapping

from .enums import TaskEventType, TaskStatus
from .errors import InvalidStatusTransitionError
from .models import AnalysisTask, TaskEvent, utc_now


LEGAL_STATUS_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.EXPLORING,
            TaskStatus.CLEANING,
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.REPORTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.EXPLORING: frozenset(
        {
            TaskStatus.CLEANING,
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.CLEANING: frozenset(
        {
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.ANALYZING: frozenset(
        {
            TaskStatus.EXPLORING,
            TaskStatus.CLEANING,
            TaskStatus.VALIDATING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.VALIDATING: frozenset(
        {
            TaskStatus.ANALYZING,
            TaskStatus.REPORTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.REPORTING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in LEGAL_STATUS_TRANSITIONS[current]


def transition_status(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if not can_transition(current, target):
        raise InvalidStatusTransitionError(current, target)
    return target


def transition_task(
    task: AnalysisTask,
    target: TaskStatus,
    *,
    message: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[AnalysisTask, TaskEvent]:
    transition_status(task.status, target)
    event_time = occurred_at or utc_now()
    updated_data = task.model_dump()
    updated_data["status"] = target
    updated_data["updated_at"] = event_time
    updated_task = AnalysisTask.model_validate(updated_data)
    event = TaskEvent(
        task_id=task.task_id,
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=task.status,
        to_status=target,
        message=message,
        occurred_at=event_time,
    )
    return updated_task, event
```

Append these exports to `domain/__init__.py`:

```python
from .state import (
    LEGAL_STATUS_TRANSITIONS,
    can_transition,
    transition_status,
    transition_task,
)
```

- [ ] **Step 4: Run state tests and the domain suite**

Run:

```powershell
python -m pytest -q tests/domain
```

Expected: all domain primitive, model and transition tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/domain tests/domain/test_state.py
git commit -m "feat: add M02 task status transitions"
```

---

### Task 4: 独立持久化记录模型与显式 mapper

**Files:**
- Create: `src/data_analysis_agent/persistence/__init__.py`
- Create: `src/data_analysis_agent/persistence/models.py`
- Create: `src/data_analysis_agent/persistence/mappers.py`
- Test: `tests/persistence/__init__.py`
- Test: `tests/persistence/test_mappers.py`

**Interfaces:**
- Produces independent `DatasetRecord`, `AnalysisTaskRecord`, `TaskEventRecord`, `ToolCallRecord`, `ExecutionResultRecord` and `ArtifactRecord` classes.
- Produces `task_to_record()`, `record_to_task()`, `event_to_record()` and `record_to_event()`.
- Persistence records use scalar/JSON-friendly fields and do not inherit from domain models.

- [ ] **Step 1: Write the failing persistence tests**

Create `tests/persistence/__init__.py` and `tests/persistence/test_mappers.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import TaskEventType, TaskStatus
from data_analysis_agent.domain.errors import PersistenceMappingError
from data_analysis_agent.domain.models import AnalysisTask, TaskEvent
from data_analysis_agent.persistence.mappers import (
    event_to_record,
    record_to_event,
    record_to_task,
    task_to_record,
)
from data_analysis_agent.persistence.models import AnalysisTaskRecord, TaskEventRecord


def test_task_record_is_distinct_and_round_trips_domain_values():
    first_dataset = uuid4()
    second_dataset = uuid4()
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    task = AnalysisTask(
        query="分析销售数据",
        dataset_ids=(first_dataset, second_dataset),
        status=TaskStatus.ANALYZING,
        max_rounds=15,
        created_at=now,
        updated_at=now,
        error_code="",
        metadata={"source": "test", "count": 2},
    )

    record = task_to_record(task)
    restored = record_to_task(record)

    assert isinstance(record, AnalysisTaskRecord)
    assert type(record) is not type(task)
    assert record.status == "ANALYZING"
    assert record.dataset_ids_json == [str(first_dataset), str(second_dataset)]
    assert restored == task


def test_task_event_record_round_trips_shared_enum_values():
    now = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    event = TaskEvent(
        task_id=uuid4(),
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=TaskStatus.PENDING,
        to_status=TaskStatus.QUEUED,
        message="queued",
        occurred_at=now,
        metadata={"worker": "offline"},
    )

    record = event_to_record(event)
    restored = record_to_event(record)

    assert isinstance(record, TaskEventRecord)
    assert record.event_type == "STATUS_CHANGED"
    assert record.from_status == "PENDING"
    assert record.to_status == "QUEUED"
    assert restored == event


def test_invalid_persistence_status_is_reported_as_mapping_error():
    record = AnalysisTaskRecord(
        task_id=uuid4(),
        query="分析",
        status="BROKEN",
    )

    with pytest.raises(PersistenceMappingError, match="status"):
        record_to_task(record)


def test_persistence_records_forbid_unknown_columns():
    with pytest.raises(ValidationError):
        AnalysisTaskRecord(query="分析", status="PENDING", unknown_column=True)
```

- [ ] **Step 2: Run the persistence tests and verify the expected red state**

Run:

```powershell
python -m pytest -q tests/persistence/test_mappers.py
```

Expected: collection fails because `data_analysis_agent.persistence` does not exist yet.

- [ ] **Step 3: Implement database-friendly Pydantic records**

Create `src/data_analysis_agent/persistence/models.py`:

```python
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class PersistenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetRecord(PersistenceModel):
    dataset_id: UUID = Field(default_factory=uuid4)
    name: StrictStr
    source_uri: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(default=0, ge=0)
    checksum: StrictStr | None = None
    created_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class AnalysisTaskRecord(PersistenceModel):
    task_id: UUID = Field(default_factory=uuid4)
    query: StrictStr
    dataset_ids_json: list[StrictStr] = Field(default_factory=list)
    status: StrictStr = "PENDING"
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error_code: StrictStr | None = None
    error_message: StrictStr | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class TaskEventRecord(PersistenceModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: StrictStr
    from_status: StrictStr | None = None
    to_status: StrictStr
    message: StrictStr | None = None
    occurred_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(PersistenceModel):
    tool_call_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    tool_name: StrictStr
    arguments_json: dict[str, Any] = Field(default_factory=dict)
    result_json: dict[str, Any] | StrictStr | None = None
    status: StrictStr = "PENDING"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: StrictStr | None = None


class ExecutionResultRecord(PersistenceModel):
    execution_result_id: UUID = Field(default_factory=uuid4)
    tool_call_id: UUID | None = None
    success: StrictBool
    output_text: StrictStr = ""
    error_text: StrictStr | None = None
    variables_json: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)


class ArtifactRecord(PersistenceModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    artifact_type: StrictStr
    name: StrictStr
    file_path: StrictStr | None = None
    format: StrictStr | None = None
    mime_type: StrictStr | None = None
    content_hash: StrictStr | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
```

- [ ] **Step 4: Implement explicit domain/record mappers**

Create `src/data_analysis_agent/persistence/mappers.py`:

```python
from pydantic import ValidationError

from ..domain.enums import ReportFormat, TaskEventType, TaskStatus
from ..domain.errors import PersistenceMappingError
from ..domain.models import AnalysisTask, TaskEvent

from .models import AnalysisTaskRecord, TaskEventRecord


def _enum_value(enum_type, value, field_name):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise PersistenceMappingError(
            f"Invalid {field_name} in persistence record"
        ) from exc


def task_to_record(task: AnalysisTask) -> AnalysisTaskRecord:
    return AnalysisTaskRecord(
        task_id=task.task_id,
        query=task.query,
        dataset_ids_json=[str(dataset_id) for dataset_id in task.dataset_ids],
        status=task.status.value,
        max_rounds=task.max_rounds,
        created_at=task.created_at,
        updated_at=task.updated_at,
        error_code=task.error_code,
        error_message=task.error_message,
        metadata_json=dict(task.metadata),
    )


def record_to_task(record: AnalysisTaskRecord) -> AnalysisTask:
    try:
        status = _enum_value(TaskStatus, record.status, "status")
        dataset_ids = tuple(record_id for record_id in record.dataset_ids_json)
        return AnalysisTask(
            task_id=record.task_id,
            query=record.query,
            dataset_ids=dataset_ids,
            status=status,
            max_rounds=record.max_rounds,
            created_at=record.created_at,
            updated_at=record.updated_at,
            error_code=record.error_code,
            error_message=record.error_message,
            metadata=dict(record.metadata_json),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, PersistenceMappingError):
            raise
        raise PersistenceMappingError(
            "Unable to map AnalysisTaskRecord to AnalysisTask"
        ) from exc


def event_to_record(event: TaskEvent) -> TaskEventRecord:
    return TaskEventRecord(
        event_id=event.event_id,
        task_id=event.task_id,
        event_type=event.event_type.value,
        from_status=event.from_status.value if event.from_status else None,
        to_status=event.to_status.value,
        message=event.message,
        occurred_at=event.occurred_at,
        metadata_json=dict(event.metadata),
    )


def record_to_event(record: TaskEventRecord) -> TaskEvent:
    try:
        return TaskEvent(
            event_id=record.event_id,
            task_id=record.task_id,
            event_type=_enum_value(TaskEventType, record.event_type, "event_type"),
            from_status=(
                _enum_value(TaskStatus, record.from_status, "from_status")
                if record.from_status
                else None
            ),
            to_status=_enum_value(TaskStatus, record.to_status, "to_status"),
            message=record.message,
            occurred_at=record.occurred_at,
            metadata=dict(record.metadata_json),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise PersistenceMappingError(
            "Unable to map TaskEventRecord to TaskEvent"
        ) from exc
```

Before running the mapper tests, correct the mapper's UUID conversion to preserve the domain UUID type:

```python
from uuid import UUID

# inside record_to_task
dataset_ids = tuple(UUID(record_id) for record_id in record.dataset_ids_json)
```

Create `src/data_analysis_agent/persistence/__init__.py`:

```python
from .mappers import event_to_record, record_to_event, record_to_task, task_to_record
from .models import (
    AnalysisTaskRecord,
    ArtifactRecord,
    DatasetRecord,
    ExecutionResultRecord,
    PersistenceModel,
    TaskEventRecord,
    ToolCallRecord,
)

__all__ = [
    "AnalysisTaskRecord",
    "ArtifactRecord",
    "DatasetRecord",
    "ExecutionResultRecord",
    "PersistenceModel",
    "TaskEventRecord",
    "ToolCallRecord",
    "event_to_record",
    "record_to_event",
    "record_to_task",
    "task_to_record",
]
```

- [ ] **Step 5: Run persistence tests, domain tests and serialization checks**

Run:

```powershell
python -m pytest -q tests/persistence tests/domain
python -m compileall -q src/data_analysis_agent/domain src/data_analysis_agent/persistence
```

Expected: all tests pass and compilation exits 0. No database or network process is started.

- [ ] **Step 6: Commit**

```powershell
git add src/data_analysis_agent/persistence tests/persistence
git commit -m "feat: add M02 persistence records and mappers"
```

---

### Task 5: API 请求/响应 DTO 与共享状态类型

**Files:**
- Create: `src/data_analysis_agent/api/__init__.py`
- Create: `src/data_analysis_agent/api/schemas.py`
- Test: `tests/api/__init__.py`
- Test: `tests/api/test_schemas.py`

**Interfaces:**
- Produces `AnalysisTaskCreateRequest`, `AnalysisTaskResponse`, `TaskEventResponse`, `ExecutionResultResponse`, `ArtifactResponse` and `ErrorResponse`.
- DTO classes are independent API models, but their `status` and status fields use `data_analysis_agent.domain.enums.TaskStatus` by identity.
- API request validation permits an empty dataset list to retain current M00 no-file support, requires a nonblank query and uses a strict positive `max_rounds`.

- [ ] **Step 1: Write the failing API DTO tests**

Create `tests/api/__init__.py` and `tests/api/test_schemas.py`:

```python
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.api.schemas import (
    AnalysisTaskCreateRequest,
    AnalysisTaskResponse,
    ArtifactResponse,
    ErrorResponse,
    ExecutionResultResponse,
    TaskEventResponse,
)
from data_analysis_agent.domain.enums import ReportFormat, TaskEventType, TaskStatus


def test_create_request_validates_and_serializes_without_a_dataset():
    request = AnalysisTaskCreateRequest(query="分析销售数据")

    assert request.dataset_ids == ()
    assert request.max_rounds == 10
    assert json.loads(request.model_dump_json())["query"] == "分析销售数据"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"query": "   "}, "query"),
        ({"query": "分析", "max_rounds": "10"}, "max_rounds"),
    ],
)
def test_create_request_rejects_invalid_field_types_and_blank_values(payload, field):
    with pytest.raises(ValidationError) as exc_info:
        AnalysisTaskCreateRequest(**payload)

    assert field in str(exc_info.value)


def test_response_uses_the_domain_task_status_enum_and_is_not_a_domain_model():
    task_id = uuid4()
    response = AnalysisTaskResponse(
        task_id=task_id,
        query="分析销售数据",
        status=TaskStatus.REPORTING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        artifacts=(
            ArtifactResponse(
                artifact_id=uuid4(),
                artifact_type="chart",
                name="trend.png",
                file_path="outputs/trend.png",
                mime_type="image/png",
            ),
        ),
    )

    assert isinstance(response.status, TaskStatus)
    assert response.status is TaskStatus.REPORTING
    assert json.loads(response.model_dump_json())["status"] == "REPORTING"
    assert response.__class__.__name__ == "AnalysisTaskResponse"


def test_event_execution_and_error_dtos_are_json_serializable():
    now = datetime.now(timezone.utc)
    event = TaskEventResponse(
        event_id=uuid4(),
        task_id=uuid4(),
        event_type=TaskEventType.STATUS_CHANGED,
        from_status=TaskStatus.ANALYZING,
        to_status=TaskStatus.VALIDATING,
        occurred_at=now,
    )
    execution = ExecutionResultResponse(
        success=False,
        output="partial",
        error="executor failed",
        variables={},
        duration_ms=4,
    )
    error = ErrorResponse(
        code="EXECUTION_FAILED",
        message="代码执行失败",
        details={"retryable": True},
    )

    for dto in (event, execution, error):
        assert isinstance(json.loads(dto.model_dump_json()), dict)

    assert event.to_status is TaskStatus.VALIDATING
    assert execution.success is False


def test_api_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ErrorResponse(code="E", message="m", unexpected="x")
```

- [ ] **Step 2: Run the API tests and verify the expected red state**

Run:

```powershell
python -m pytest -q tests/api/test_schemas.py
```

Expected: collection fails because `data_analysis_agent.api` does not exist yet.

- [ ] **Step 3: Implement independent API schemas**

Create `src/data_analysis_agent/api/schemas.py`:

```python
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from ..domain.enums import ReportFormat, TaskEventType, TaskStatus


def _nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AnalysisTaskCreateRequest(APIModel):
    query: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    max_rounds: StrictInt = Field(default=10, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)


class ErrorResponse(APIModel):
    code: StrictStr
    message: StrictStr
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactResponse(APIModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    artifact_type: StrictStr
    name: StrictStr
    file_path: StrictStr | None = None
    format: ReportFormat | None = None
    mime_type: StrictStr | None = None
    description: StrictStr | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisTaskResponse(APIModel):
    task_id: UUID
    query: StrictStr
    dataset_ids: tuple[UUID, ...] = ()
    status: TaskStatus
    max_rounds: StrictInt = Field(default=10, gt=0)
    created_at: datetime
    updated_at: datetime
    error: ErrorResponse | None = None
    artifacts: tuple[ArtifactResponse, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_query = field_validator("query")(_nonblank)


class TaskEventResponse(APIModel):
    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    event_type: TaskEventType
    from_status: TaskStatus | None = None
    to_status: TaskStatus
    message: StrictStr | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResultResponse(APIModel):
    success: StrictBool
    output: StrictStr = ""
    error: StrictStr | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    duration_ms: StrictInt | None = Field(default=None, ge=0)
```

Create `src/data_analysis_agent/api/__init__.py`:

```python
from .schemas import (
    APIModel,
    AnalysisTaskCreateRequest,
    AnalysisTaskResponse,
    ArtifactResponse,
    ErrorResponse,
    ExecutionResultResponse,
    TaskEventResponse,
)

__all__ = [
    "APIModel",
    "AnalysisTaskCreateRequest",
    "AnalysisTaskResponse",
    "ArtifactResponse",
    "ErrorResponse",
    "ExecutionResultResponse",
    "TaskEventResponse",
]
```

- [ ] **Step 4: Run API, persistence and domain tests**

Run:

```powershell
python -m pytest -q tests/api tests/persistence tests/domain
```

Expected: all model, state, mapper and DTO tests pass; `TaskStatus` is imported from one canonical enum module.

- [ ] **Step 5: Commit**

```powershell
git add src/data_analysis_agent/api tests/api
git commit -m "feat: add M02 API request and response DTOs"
```

---

### Task 6: 三层边界契约、安装元数据与完整回归

**Files:**
- Test: `tests/m02/__init__.py`
- Test: `tests/m02/test_layer_contract.py`
- Verify: `pyproject.toml`, `src/data_analysis_agent/domain/`, `src/data_analysis_agent/persistence/`, `src/data_analysis_agent/api/`

**Interfaces:**
- Verifies domain, persistence and API imports are stable and distinct.
- Verifies Agent/Worker/API future consumers can import the same `TaskStatus`; no runtime consumer is added in this task.
- Verifies M01 installation, CLI, package import, no-key behavior and all prior M00 contracts remain intact.

- [ ] **Step 1: Write the cross-layer contract tests**

Create `tests/m02/__init__.py` and `tests/m02/test_layer_contract.py`:

```python
import json
from datetime import datetime, timezone
from uuid import uuid4

from data_analysis_agent.api import AnalysisTaskResponse
from data_analysis_agent.domain import AnalysisTask, TaskStatus
from data_analysis_agent.persistence import AnalysisTaskRecord, task_to_record


def test_domain_persistence_and_api_models_are_distinct_layers():
    task = AnalysisTask(task_id=uuid4(), query="分析", status=TaskStatus.ANALYZING)
    record = task_to_record(task)
    response = AnalysisTaskResponse(
        task_id=task.task_id,
        query=task.query,
        status=TaskStatus.ANALYZING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    assert type(task) is not type(record)
    assert type(task) is not type(response)
    assert isinstance(record, AnalysisTaskRecord)
    assert response.status is TaskStatus.ANALYZING
    assert TaskStatus.__module__ == "data_analysis_agent.domain.enums"


def test_all_three_boundaries_are_json_serializable():
    task = AnalysisTask(query="分析")
    record = task_to_record(task)
    response = AnalysisTaskResponse(
        task_id=task.task_id,
        query=task.query,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )

    for model in (task, record, response):
        assert isinstance(json.loads(model.model_dump_json()), dict)
```

- [ ] **Step 2: Run the cross-layer tests and inspect the expected package boundary**

Run:

```powershell
python -m pytest -q tests/m02/test_layer_contract.py
python -m compileall -q src tests
```

Expected: all cross-layer tests pass and compileall exits 0.

- [ ] **Step 3: Run the complete no-key verification suite**

Run from the repository root:

```powershell
$env:OPENAI_API_KEY = ""
$env:OPENAI_BASE_URL = ""
$env:OPENAI_MODEL = ""
python -m pytest -q
python -m data_analysis_agent --help
python main.py --help
python -m pip check
git diff --check
```

Expected: all pre-existing M00/M01 tests and M02 tests pass; help commands do not require an API key; pip check reports `No broken requirements found.`; diff check has no whitespace error.

- [ ] **Step 4: Verify external import and dependency metadata**

Run:

```powershell
$external = Join-Path $env:TEMP "data-analysis-agent-m02-external"
New-Item -ItemType Directory -Force -Path $external | Out-Null
$python = (Get-Command python).Source
Push-Location $external
try {
    $env:PYTHONPATH = ""
    & $python -c "import data_analysis_agent; from data_analysis_agent.domain import TaskStatus; print(data_analysis_agent.__file__); print(TaskStatus.__module__)"
} finally {
    Pop-Location
}
```

Expected: the installed/imported package resolves to `src/data_analysis_agent/__init__.py`, and `TaskStatus.__module__` is `data_analysis_agent.domain.enums`. Do not delete a broad directory; the temporary directory is explicitly named and may be left if the host owns it.

- [ ] **Step 5: Update project progress records**

Update the M02 section of `task_plan.md`, `progress.md` and `findings.md` with:

- the implementation-plan commit and M02 task commits;
- the final number of passing tests;
- the exact no-key command results;
- the fact that Agent/Worker/API runtime orchestration was not changed;
- any environment-only limitation observed during verification.

Do not add API keys, generated reports, virtual environments or temporary external directories to Git.

- [ ] **Step 6: Commit the cross-layer tests and progress records**

```powershell
git add tests/m02 task_plan.md progress.md findings.md
git commit -m "test: verify M02 model layer boundaries"
```

---

## Final acceptance checklist

- [ ] `pydantic>=2.0,<3.0` is an explicit runtime dependency.
- [ ] All nine requested domain models exist under `src/data_analysis_agent/domain/models.py`.
- [ ] Invalid enum values, extra fields, blank required strings and strict type violations produce clear Pydantic errors.
- [ ] Every model serializes with `model_dump_json()`.
- [ ] `TaskStatus` is defined exactly once and imported by domain, persistence mappers and API DTOs.
- [ ] The legal transition matrix is explicit, tested and raises `InvalidStatusTransitionError` for illegal transitions.
- [ ] Transitioning a task returns a new task and a structured `STATUS_CHANGED` event without mutating the input.
- [ ] Persistence records are independent classes and round-trip task/event values through explicit mappers.
- [ ] API request/response DTOs are independent classes and do not call runtime services.
- [ ] M00/M01 runtime behavior and public top-level package exports remain unchanged.
- [ ] Empty API environment full regression passes with no network or real model API.
- [ ] `compileall`, `pip check`, `git diff --check`, module help and external import checks pass.
