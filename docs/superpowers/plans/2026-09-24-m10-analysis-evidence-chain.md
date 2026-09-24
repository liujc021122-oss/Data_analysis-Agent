# M10 Analysis Evidence Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every report-facing metric and chart traceable to a dataset, execution, and code hash, and mark unsupported facts or missing files as pending confirmation without breaking the legacy Agent result contract.

**Architecture:** Extend the existing frozen Pydantic domain artifacts with optional backward-compatible provenance and verification fields. Add a task-scoped in-memory `EvidenceRegistry` that owns registration, deduplication, recomputation, chart path checks, claim validation, and numeric report validation. Inject the registry into `DataAnalysisAgent`, keep the existing dictionary-shaped result, and add a JSON-safe evidence snapshot beside it.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, pathlib, UUID, `math.isclose`, standard-library regular expressions; no new runtime dependency, database table, real model API, network service, or Docker requirement.

## Global Constraints

- M10 only extends evidence models, in-memory evidence validation, execution IDs, and Agent integration; M11 report rendering and M03 persistence migrations remain unchanged.
- Existing `MetricArtifact`, `ChartArtifact`, `AgentState`, `DataAnalysisAgent`, and `quick_analysis` constructors and legacy result keys remain usable.
- All new domain models use the existing `DomainModel` rules: `extra="forbid"`, frozen snapshots, strict types, finite numbers, recursive JSON-safe fields, and timezone-aware UTC timestamps.
- A metric may become `VERIFIED` only when it has the current task ID, at least one dataset ID, an execution ID, and a valid SHA-256 code hash.
- A chart is report-eligible only when its resolved path is a regular file within the configured task output root; absolute host paths never enter model prompts or error messages.
- Tests use fake LLMs, fake executors, temporary files, and in-memory registries; they never require an API key, network access, Docker, or a database server.
- Every production-code change follows RED → verify expected failure → minimal GREEN implementation → focused GREEN test → commit.

---

### Task 1: Evidence domain contracts

**Files:**
- Modify: `src/data_analysis_agent/domain/enums.py`
- Modify: `src/data_analysis_agent/domain/errors.py`
- Modify: `src/data_analysis_agent/domain/models.py`
- Modify: `src/data_analysis_agent/domain/__init__.py`
- Create: `tests/domain/test_evidence_models.py`

**Interfaces:**
- Produces `EvidenceVerificationStatus`, `EvidenceClaimKind`, and `EvidenceClaimStatus` string enums.
- Produces `EvidenceErrorCode`, `EvidenceError`, and `EvidenceReferenceError` with stable `.code` values.
- Extends `MetricArtifact` with `task_id`, `formula`, `source_columns`, `source_dataset_ids`, `execution_id`, `code_hash`, `computed_at`, `verification_status`, `recomputed_value`, and `tolerance`.
- Extends `ChartArtifact` with `task_id`, `chart_type`, `source_metric_ids`, `execution_id`, `code_hash`, `verification_status`, and `checked_at`.
- Produces `EvidenceClaim` and `EvidenceValidation` models that can be serialized by `model_dump_json()`.

- [ ] **Step 1: Write the failing model tests.**

Add the following tests to `tests/domain/test_evidence_models.py`:

```python
import json
from datetime import datetime
from math import inf, nan
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.domain.enums import (
    EvidenceClaimKind,
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)
from data_analysis_agent.domain.models import (
    ChartArtifact,
    EvidenceClaim,
    EvidenceValidation,
    MetricArtifact,
)


def test_metric_keeps_legacy_defaults_and_serializes_provenance():
    task_id = uuid4()
    dataset_id = uuid4()
    execution_id = uuid4()
    metric = MetricArtifact(
        task_id=task_id,
        name="revenue",
        value=125.0,
        unit="CNY",
        formula="sum(revenue)",
        source_columns=("revenue",),
        source_dataset_ids=(dataset_id,),
        execution_id=execution_id,
        code_hash="a" * 64,
        verification_status=EvidenceVerificationStatus.UNVERIFIED,
    )

    encoded = json.loads(metric.model_dump_json())

    assert encoded["task_id"] == str(task_id)
    assert encoded["source_dataset_ids"] == [str(dataset_id)]
    assert encoded["execution_id"] == str(execution_id)
    assert encoded["verification_status"] == "UNVERIFIED"
    assert MetricArtifact(name="legacy", value=1.0).task_id is None


def test_evidence_models_reject_unknown_fields_invalid_status_and_nonfinite_values():
    with pytest.raises(ValidationError, match="verification_status"):
        MetricArtifact(name="metric", value=1.0, verification_status="BROKEN")
    with pytest.raises(ValidationError, match="value"):
        MetricArtifact(name="metric", value=nan)
    with pytest.raises(ValidationError, match="recomputed_value"):
        MetricArtifact(name="metric", value=1.0, recomputed_value=inf)
    with pytest.raises(ValidationError, match="unexpected"):
        EvidenceClaim(
            task_id=uuid4(),
            text="fact",
            kind=EvidenceClaimKind.FACT,
            unexpected=True,
        )


def test_claim_and_validation_are_strict_and_json_serializable():
    task_id = uuid4()
    claim = EvidenceClaim(
        task_id=task_id,
        text="Revenue is supported by the verified metric.",
        kind=EvidenceClaimKind.FACT,
        status=EvidenceClaimStatus.SUPPORTED,
    )
    validation = EvidenceValidation(
        task_id=task_id,
        valid=True,
        claims=(claim,),
        unsupported_numeric_claims=(),
        missing_chart_ids=(),
        error_codes=(),
    )

    assert json.loads(validation.model_dump_json())["claims"][0]["status"] == "SUPPORTED"


def test_evidence_timestamps_require_timezone_information():
    with pytest.raises(ValidationError, match="computed_at"):
        MetricArtifact(
            name="metric",
            value=1.0,
            computed_at=datetime(2026, 9, 24, 10, 0),
        )
    with pytest.raises(ValidationError, match="checked_at"):
        ChartArtifact(
            filename="chart.png",
            file_path="chart.png",
            checked_at=datetime(2026, 9, 24, 10, 0),
        )
```

- [ ] **Step 2: Run the model tests to verify the failure is caused by missing evidence contracts.**

Run:

```powershell
pytest tests/domain/test_evidence_models.py -q
```

Expected: collection fails because the evidence enums/models are not yet exported. Do not implement production code until this failure is observed.

- [ ] **Step 3: Implement the minimal domain contracts.**

Add these enum values and model shapes, preserving all existing fields and defaults:

```python
class EvidenceVerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"


class EvidenceClaimKind(str, Enum):
    FACT = "FACT"
    INTERPRETATION = "INTERPRETATION"


class EvidenceClaimStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
```

Use `task_id: UUID | None = None`, `source_columns: tuple[StrictStr, ...] = ()`, `source_dataset_ids: tuple[UUID, ...] = ()`, and `execution_id: UUID | None = None` for compatibility. Use `code_hash: StrictStr | None = None` with the existing 64-hex digest rule. Add `computed_at` and `checked_at` to the domain time normalization list. `EvidenceValidation` must contain `task_id`, `valid`, `claims`, `unsupported_numeric_claims`, `missing_chart_ids`, `error_codes`, and `checked_at`.

Add stable errors without including file paths or payloads:

```python
class EvidenceErrorCode(str, Enum):
    EVIDENCE_TASK_MISMATCH = "EVIDENCE_TASK_MISMATCH"
    METRIC_CONFLICT = "METRIC_CONFLICT"
    METRIC_NOT_REPRODUCIBLE = "METRIC_NOT_REPRODUCIBLE"
    CHART_PATH_INVALID = "CHART_PATH_INVALID"
    CHART_NOT_FOUND = "CHART_NOT_FOUND"
    UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
    EVIDENCE_REFERENCE_NOT_FOUND = "EVIDENCE_REFERENCE_NOT_FOUND"


class EvidenceError(DomainError):
    def __init__(self, code: EvidenceErrorCode, message: str):
        self.code = code
        super().__init__(message)


class EvidenceReferenceError(EvidenceError):
    """A claim references an unknown metric or chart artifact."""
```

Export every new enum, error, and model from `domain/__init__.py` and validate all new fields through the existing `DomainModel` validators.

- [ ] **Step 4: Run focused and existing domain tests.**

Run:

```powershell
pytest tests/domain/test_evidence_models.py tests/domain/test_models.py -q
```

Expected: all new evidence tests and all pre-existing domain tests pass.

- [ ] **Step 5: Commit the domain contract.**

```powershell
git add src/data_analysis_agent/domain tests/domain/test_evidence_models.py
git commit -m "feat: add evidence domain contracts"
```

### Task 2: Metric registry, deduplication, and recomputation

**Files:**
- Create: `src/data_analysis_agent/services/evidence.py`
- Modify: `src/data_analysis_agent/services/__init__.py`
- Modify: `src/data_analysis_agent/__init__.py`
- Create: `tests/services/test_evidence_registry.py`

**Interfaces:**
- `EvidenceRegistry(task_id: UUID, output_root: Path | None = None, absolute_tolerance: float = 1e-9, relative_tolerance: float = 1e-6)`.
- `register_metric(metric: MetricArtifact) -> MetricArtifact`.
- `verify_metric(metric_id: UUID, recomputed_value: float) -> MetricArtifact`.
- `snapshot(task_id: UUID) -> dict[str, tuple[...]]` with `metrics`, `charts`, `claims`, and optional `validation` keys.
- `EvidenceRegistry` is task-scoped and owns immutable replacement snapshots; it does not write to a database.

- [ ] **Step 1: Write failing registry tests for task isolation, idempotency, and recomputation.**

Add these tests:

```python
from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.errors import EvidenceError, EvidenceErrorCode
from data_analysis_agent.domain.models import MetricArtifact
from data_analysis_agent.services.evidence import EvidenceRegistry


def reproducible_metric(task_id, *, value=10.0, name="revenue"):
    return MetricArtifact(
        task_id=task_id,
        name=name,
        value=value,
        unit="CNY",
        formula="sum(revenue)",
        source_columns=("revenue",),
        source_dataset_ids=(uuid4(),),
        execution_id=uuid4(),
        code_hash="b" * 64,
    )


def test_register_metric_is_idempotent_for_same_fact():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    first = reproducible_metric(task_id)

    registered = registry.register_metric(first)
    repeated = registry.register_metric(first.model_copy(update={"artifact_id": uuid4()}))

    assert repeated.artifact_id == registered.artifact_id
    assert len(registry.snapshot(task_id)["metrics"]) == 1


def test_register_metric_rejects_conflicting_value_for_same_fact():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    original = reproducible_metric(task_id, value=10.0)
    registry.register_metric(original)
    conflict = original.model_copy(update={"artifact_id": uuid4(), "value": 11.0})

    with pytest.raises(EvidenceError) as exc_info:
        registry.register_metric(conflict)

    assert exc_info.value.code is EvidenceErrorCode.METRIC_CONFLICT


def test_verify_metric_requires_provenance_and_records_recomputed_value():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))

    verified = registry.verify_metric(metric.artifact_id, 10.0 + 1e-10)

    assert verified.verification_status is EvidenceVerificationStatus.VERIFIED
    assert verified.recomputed_value == pytest.approx(10.0 + 1e-10)


def test_verify_metric_marks_mismatch_pending_and_raises_stable_error():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))

    with pytest.raises(EvidenceError) as exc_info:
        registry.verify_metric(metric.artifact_id, 99.0)

    assert exc_info.value.code is EvidenceErrorCode.METRIC_NOT_REPRODUCIBLE
    assert registry.snapshot(task_id)["metrics"][0].verification_status is EvidenceVerificationStatus.PENDING_CONFIRMATION


def test_registry_rejects_cross_task_metric_without_exposing_paths():
    registry = EvidenceRegistry(task_id=uuid4())
    metric = reproducible_metric(uuid4())

    with pytest.raises(EvidenceError) as exc_info:
        registry.register_metric(metric)

    assert exc_info.value.code is EvidenceErrorCode.EVIDENCE_TASK_MISMATCH
    assert "E:\\" not in str(exc_info.value)
```

- [ ] **Step 2: Run the registry tests and confirm the service is absent.**

Run:

```powershell
pytest tests/services/test_evidence_registry.py -q
```

Expected: collection fails because `EvidenceRegistry` is not defined. If the test file has a syntax or import error unrelated to the missing service, fix the test before writing production code.

- [ ] **Step 3: Implement the metric registry minimally.**

Use a private mapping keyed by the immutable fact identity `(name, unit, formula, execution_id, source_dataset_ids, source_columns)`, and a second mapping by artifact ID. Enforce the current task before either map is changed. The essential implementation shape is:

```python
class EvidenceRegistry:
    def __init__(self, *, task_id, output_root=None, absolute_tolerance=1e-9, relative_tolerance=1e-6):
        self.task_id = task_id
        self.output_root = Path(output_root).resolve(strict=False) if output_root else None
        self.absolute_tolerance = absolute_tolerance
        self.relative_tolerance = relative_tolerance
        self._metrics = {}
        self._metric_keys = {}
        self._charts = {}
        self._claims = {}
        self._validation = None

    def _require_task(self, value):
        if value != self.task_id:
            raise EvidenceError(EvidenceErrorCode.EVIDENCE_TASK_MISMATCH, "evidence belongs to another task")

    @staticmethod
    def _metric_key(metric):
        return (
            metric.name, metric.unit, metric.formula, metric.execution_id,
            metric.source_dataset_ids, metric.source_columns,
        )

    def register_metric(self, metric):
        self._require_task(metric.task_id)
        key = self._metric_key(metric)
        existing_id = self._metric_keys.get(key)
        if existing_id is not None:
            existing = self._metrics[existing_id]
            if not math.isclose(existing.value, metric.value, rel_tol=self.relative_tolerance, abs_tol=self.absolute_tolerance):
                raise EvidenceError(EvidenceErrorCode.METRIC_CONFLICT, "metric fact conflicts with a registered value")
            return existing
        if metric.verification_status is EvidenceVerificationStatus.VERIFIED and not self._has_provenance(metric):
            raise EvidenceError(EvidenceErrorCode.METRIC_NOT_REPRODUCIBLE, "verified metric is missing provenance")
        self._metrics[metric.artifact_id] = metric
        self._metric_keys[key] = metric.artifact_id
        return metric
```

`verify_metric` must replace the frozen artifact with `model_copy(update={...})`; a mismatch replaces its status with `PENDING_CONFIRMATION` before raising `METRIC_NOT_REPRODUCIBLE`. `_has_provenance` must require the task ID, non-empty dataset IDs, execution ID, and a 64-hex code hash. `snapshot()` must first validate the requested task and return tuples of the current immutable models.

- [ ] **Step 4: Run focused tests and the service import contract.**

Run:

```powershell
pytest tests/services/test_evidence_registry.py tests/domain/test_evidence_models.py -q
python -c "from data_analysis_agent import EvidenceRegistry; print(EvidenceRegistry.__name__)"
```

Expected: all focused tests pass and the public import prints `EvidenceRegistry`.

- [ ] **Step 5: Commit metric registry behavior.**

```powershell
git add src/data_analysis_agent/services src/data_analysis_agent/__init__.py tests/services/test_evidence_registry.py
git commit -m "feat: add metric evidence registry"
```

### Task 3: Chart checks, claims, and report numeric validation

**Files:**
- Modify: `src/data_analysis_agent/services/evidence.py`
- Modify: `tests/services/test_evidence_registry.py`

**Interfaces:**
- `register_chart(chart: ChartArtifact) -> ChartArtifact`.
- `check_chart(chart_id: UUID) -> ChartArtifact`; successful checks set `VERIFIED`, invalid/missing checks set `PENDING_CONFIRMATION` and raise the stable chart error.
- `validate_claim(claim: EvidenceClaim) -> EvidenceClaim`.
- `validate_report(markdown: str, task_id: UUID) -> EvidenceValidation`.
- `validate_report` ignores fenced code blocks, URLs, UUIDs, and Markdown image target paths when extracting numeric claims; it compares remaining finite numbers only with verified metric values using configured absolute/relative tolerance.

- [ ] **Step 1: Write failing tests for chart boundaries, claim status, and report numbers.**

Add these tests:

```python
from data_analysis_agent.domain.enums import (
    EvidenceClaimKind,
    EvidenceClaimStatus,
    EvidenceVerificationStatus,
)
from data_analysis_agent.domain.models import ChartArtifact, EvidenceClaim


def test_chart_check_accepts_only_regular_files_inside_output_root(tmp_path):
    task_id = uuid4()
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"png")
    registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    chart = registry.register_chart(
        ChartArtifact(
            task_id=task_id,
            filename="chart.png",
            file_path=str(chart_path),
            chart_type="line",
        )
    )

    checked = registry.check_chart(chart.artifact_id)

    assert checked.verification_status is EvidenceVerificationStatus.VERIFIED


@pytest.mark.parametrize("file_path", ["missing.png", ".", "../outside.png"])
def test_chart_check_keeps_invalid_chart_pending(tmp_path, file_path):
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    chart = registry.register_chart(
        ChartArtifact(task_id=task_id, filename="chart.png", file_path=file_path)
    )

    with pytest.raises(EvidenceError) as exc_info:
        registry.check_chart(chart.artifact_id)

    assert exc_info.value.code in {
        EvidenceErrorCode.CHART_PATH_INVALID,
        EvidenceErrorCode.CHART_NOT_FOUND,
    }
    assert registry.snapshot(task_id)["charts"][0].verification_status is EvidenceVerificationStatus.PENDING_CONFIRMATION


def test_fact_requires_verified_evidence_but_interpretation_can_be_pending():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id))
    fact = EvidenceClaim(
        task_id=task_id,
        text="Revenue is verified.",
        kind=EvidenceClaimKind.FACT,
        metric_ids=(metric.artifact_id,),
    )
    interpretation = EvidenceClaim(
        task_id=task_id,
        text="The trend may indicate seasonality.",
        kind=EvidenceClaimKind.INTERPRETATION,
    )

    assert registry.validate_claim(fact).status is EvidenceClaimStatus.PENDING_CONFIRMATION
    assert registry.validate_claim(interpretation).status is EvidenceClaimStatus.PENDING_CONFIRMATION
    registry.verify_metric(metric.artifact_id, 10.0)
    assert registry.validate_claim(fact).status is EvidenceClaimStatus.SUPPORTED


def test_report_numeric_validation_accepts_verified_values_and_flags_unknown_numbers():
    task_id = uuid4()
    registry = EvidenceRegistry(task_id=task_id)
    metric = registry.register_metric(reproducible_metric(task_id, value=10.0))
    registry.verify_metric(metric.artifact_id, 10.0)

    validation = registry.validate_report(
        "Revenue was 10. The identifier is 123e4567-e89b-12d3-a456-426614174000.\n"
        "```python\nprint(999)\n```\n",
        task_id,
    )
    unsupported = registry.validate_report("Revenue was 77.", task_id)

    assert validation.valid is True
    assert unsupported.valid is False
    assert "77" in unsupported.unsupported_numeric_claims
    assert EvidenceErrorCode.UNSUPPORTED_NUMERIC_CLAIM.value in unsupported.error_codes
```

- [ ] **Step 2: Run the new tests and confirm the missing methods fail.**

Run:

```powershell
pytest tests/services/test_evidence_registry.py -q
```

Expected: the metric tests from Task 2 pass, while chart/claim/report tests fail with missing registry methods or missing model fields.

- [ ] **Step 3: Implement chart registration and path checks.**

`register_chart` must enforce the current task and store the immutable artifact. `check_chart` must resolve `chart.file_path` against `output_root`, reject a resolved path outside the root, reject a directory, and reject a missing path. It must never include the resolved host path in the raised message:

```python
def check_chart(self, chart_id):
    chart = self._charts[chart_id]
    try:
        if self.output_root is None:
            raise EvidenceError(EvidenceErrorCode.CHART_PATH_INVALID, "chart output root is unavailable")
        candidate = Path(chart.file_path)
        if not candidate.is_absolute():
            candidate = self.output_root / candidate
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(self.output_root)
        if not resolved.is_file():
            raise EvidenceError(EvidenceErrorCode.CHART_NOT_FOUND, "chart file was not found")
    except EvidenceError:
        self._charts[chart_id] = chart.model_copy(update={"verification_status": EvidenceVerificationStatus.PENDING_CONFIRMATION})
        raise
    except (OSError, RuntimeError, ValueError):
        self._charts[chart_id] = chart.model_copy(update={"verification_status": EvidenceVerificationStatus.PENDING_CONFIRMATION})
        raise EvidenceError(EvidenceErrorCode.CHART_PATH_INVALID, "chart path is outside the task output")
    checked = chart.model_copy(update={"verification_status": EvidenceVerificationStatus.VERIFIED, "checked_at": utc_now()})
    self._charts[chart_id] = checked
    return checked
```

- [ ] **Step 4: Implement claim validation and numeric extraction.**

`validate_claim` must reject unknown IDs with `EVIDENCE_REFERENCE_NOT_FOUND`, mark a fact supported when at least one referenced metric or chart is verified, and mark an interpretation supported only when it has at least one verified reference. Claims without support are stored as `PENDING_CONFIRMATION`.

Implement report extraction in three deterministic passes: remove fenced blocks using `re.sub(r"```.*?```", "", text, flags=re.S)`, remove URLs and Markdown image targets, remove UUID-shaped tokens, then match `(?<![\w.])-?(?:\d+(?:\.\d+)?|\.\d+)%?`. Convert the match without `%` to `float`; compare with verified metric values using both tolerances. Return an `EvidenceValidation` instead of raising for unsupported numbers, with `valid=False`, the unsupported token strings, and `UNSUPPORTED_NUMERIC_CLAIM` in `error_codes`. Unknown task IDs still raise `EVIDENCE_TASK_MISMATCH`.

- [ ] **Step 5: Run the service tests and verify no host path leaks.**

Run:

```powershell
pytest tests/services/test_evidence_registry.py -q
```

Expected: all registry tests pass; failure messages contain stable codes/messages but no `tmp_path` absolute string.

- [ ] **Step 6: Commit the evidence validation service.**

```powershell
git add src/data_analysis_agent/services/evidence.py tests/services/test_evidence_registry.py
git commit -m "feat: validate chart and report evidence"
```

### Task 4: Execution IDs and Agent evidence integration

**Files:**
- Modify: `src/data_analysis_agent/execution/models.py`
- Modify: `src/data_analysis_agent/execution/agent_session.py`
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/agent/legacy_adapter.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`
- Modify: `src/data_analysis_agent/__init__.py`
- Create: `tests/agent/test_evidence_integration.py`
- Modify: `tests/agent/test_execution_backend_integration.py`

**Interfaces:**
- `ExecutionAudit.execution_id: UUID` is generated for each execution and is included in the existing audit dictionary.
- `DataAnalysisAgent(..., evidence_registry: EvidenceRegistry | None = None)` accepts an optional task-scoped registry.
- `DataAnalysisAgent.register_metric(metric: MetricArtifact) -> MetricArtifact` fills a missing task ID with the current Agent task for compatibility, then delegates to the registry.
- `DataAnalysisAgent.validate_claim(claim: EvidenceClaim) -> EvidenceClaim` delegates to the registry.
- Final Agent results retain all existing keys and add `metric_artifacts`, `chart_artifacts`, `evidence_claims`, and `evidence_validation` as JSON-safe values.

- [ ] **Step 1: Write failing Agent integration tests.**

Add tests that use a temporary output file and no real LLM:

```python
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.agent.core import DataAnalysisAgent
from data_analysis_agent.domain.enums import EvidenceVerificationStatus
from data_analysis_agent.domain.models import MetricArtifact
from data_analysis_agent.execution.agent_session import AgentExecutionSession
from data_analysis_agent.services.evidence import EvidenceRegistry
from tests.agent.test_execution_backend_integration import RecordingBackend


class FakePathResolver:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve_output_path(self, value):
        candidate = Path(value)
        if str(candidate).startswith("/output/"):
            candidate = self.root / str(candidate).removeprefix("/output/")
        elif not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(self.root)
        return resolved


def make_recording_session(tmp_path):
    return AgentExecutionSession(
        backend=RecordingBackend(),
        output_dir=tmp_path / "output",
        output_scope=tmp_path,
        task_id=uuid4(),
    )


def test_execution_audit_has_unique_execution_id(tmp_path):
    session = make_recording_session(tmp_path)
    try:
        first = session.execute_code("print('first')")
        second = session.execute_code("print('second')")
    finally:
        session.close()

    assert first["audit"]["execution_id"]
    assert first["audit"]["execution_id"] != second["audit"]["execution_id"]


def test_agent_registers_metric_and_chart_and_returns_evidence_snapshot(tmp_path):
    task_id = uuid4()
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"png")
    agent = object.__new__(DataAnalysisAgent)
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent._provided_evidence_registry = agent.evidence_registry
    agent.analysis_results = []
    agent.execution_audits = []
    agent._display_text = str
    agent.executor = FakePathResolver(tmp_path)

    metric = agent.register_metric(
        MetricArtifact(name="revenue", value=10.0, source_dataset_ids=(uuid4(),), task_id=task_id)
    )
    assert metric.task_id == task_id
    collected = agent._handle_collect_figures(
        "",
        {"figures_to_collect": [{"figure_number": 1, "filename": "chart.png", "file_path": str(chart_path)}]},
    )

    assert collected["collected_figures"][0]["filename"] == "chart.png"
    snapshot = agent.evidence_registry.snapshot(task_id)
    assert len(snapshot["charts"]) == 1
    assert snapshot["charts"][0].verification_status is EvidenceVerificationStatus.VERIFIED


def test_final_report_prompt_contains_only_verified_metric_context(tmp_path):
    task_id = uuid4()
    agent = object.__new__(DataAnalysisAgent)
    agent.task_id = task_id
    agent.evidence_registry = EvidenceRegistry(task_id=task_id, output_root=tmp_path)
    agent.current_round = 1
    agent.session_output_dir = tmp_path
    agent.analysis_results = []
    metric = agent.register_metric(
        MetricArtifact(
            task_id=task_id,
            name="revenue",
            value=10.0,
            formula="sum(revenue)",
            source_dataset_ids=(uuid4(),),
            execution_id=uuid4(),
            code_hash="c" * 64,
        )
    )
    agent.evidence_registry.verify_metric(metric.artifact_id, 10.0)

    prompt = agent._build_final_report_prompt([])

    assert "revenue" in prompt
    assert "10.0" in prompt
    assert "verified" in prompt.lower()
```

The helper `make_recording_session` should reuse the existing `RecordingBackend` from `tests/agent/test_execution_backend_integration.py`; `FakePathResolver.resolve_output_path` must resolve only under `tmp_path` and return a `Path`.

- [ ] **Step 2: Run the integration tests and observe missing execution/evidence hooks.**

Run:

```powershell
pytest tests/agent/test_evidence_integration.py tests/agent/test_execution_backend_integration.py -q
```

Expected: existing M09 tests pass, while the new tests fail because `ExecutionAudit` has no ID, the Agent has no evidence registry hooks, or the prompt has no evidence context.

- [ ] **Step 3: Add execution IDs without changing the execution protocol.**

Add `execution_id: UUID = Field(default_factory=uuid4)` to `ExecutionAudit`, pass no new backend request field, and let `AgentExecutionSession._record_audit()` construct the audit with the default. Existing `model_dump(mode="json")` automatically exposes the ID in `result["audit"]` and `self.audit_records`.

- [ ] **Step 4: Add task-scoped registry lifecycle and explicit Agent registration APIs.**

Import `EvidenceRegistry`, `EvidenceClaim`, `MetricArtifact`, and `ChartArtifact` into `agent/core.py`. Add the optional constructor argument and store `_provided_evidence_registry`. After `self.task_id` is selected in `_analyze_impl`, create a fresh registry for generated tasks or validate the injected registry task. Reset registry claims/validation only by creating a new task-scoped registry; never reuse a previous task registry.

Implement:

```python
def register_metric(self, metric: MetricArtifact) -> MetricArtifact:
    if metric.task_id is None:
        metric = metric.model_copy(update={"task_id": self.task_id})
    return self.evidence_registry.register_metric(metric)


def validate_claim(self, claim: EvidenceClaim) -> EvidenceClaim:
    return self.evidence_registry.validate_claim(claim)
```

When `_handle_generate_code` receives an execution audit, save its `execution_id` and `code_sha256` as the most recent successful execution provenance for chart registration.

- [ ] **Step 5: Register valid charts and inject verified evidence into final reports.**

In `_handle_collect_figures`, keep the existing compatibility dictionary, resolve the executor path first, create a `ChartArtifact` with the current task ID, filename/path, description/title, latest execution ID/hash, and chart type from the payload, register it, and call `check_chart`. If the check raises a chart error, omit that figure from the report-eligible collection while retaining a pending chart in the registry; this prevents invalid Markdown links. Existing invalid container paths must remain dropped exactly as the M09 tests expect.

Update `_build_final_report_prompt` to append a deterministic evidence section built only from `VERIFIED` metrics and charts:

```text
结构化证据（只能引用以下已验证事实）:
- metric_id=<uuid> name=<name> value=<value> unit=<unit> formula=<formula>
  datasets=<dataset ids> execution_id=<execution id> code_hash=<sha256>
- chart_id=<uuid> filename=<filename> chart_type=<type>
```

Do not include unverified values, host paths, or raw model output in this section.

- [ ] **Step 6: Include evidence snapshots in the final legacy result.**

At the end of `_generate_final_report`, call `registry.validate_report(final_report_content, self.task_id)` and add these keys without removing existing keys:

```python
"metric_artifacts": [item.model_dump(mode="json") for item in registry.snapshot(task_id)["metrics"]],
"chart_artifacts": [item.model_dump(mode="json") for item in registry.snapshot(task_id)["charts"]],
"evidence_claims": [item.model_dump(mode="json") for item in registry.snapshot(task_id)["claims"]],
"evidence_validation": validation.model_dump(mode="json"),
```

If report generation itself fails, preserve the existing exception and report fallback behavior; evidence validation must never discard a successful analysis result. Update `LegacyAnalysisAdapter.to_legacy_result()` to use these keys from `_generate_final_report` or the Agent attributes when the report output is built, so compatibility callers receive the same evidence data.

- [ ] **Step 7: Run Agent and execution regressions.**

Run:

```powershell
pytest tests/agent/test_evidence_integration.py tests/agent/test_execution_backend_integration.py tests/contract/test_agent_contract.py -q
```

Expected: all new tests and existing Agent/execution contracts pass, including max-round, invalid-path, and legacy-result tests.

- [ ] **Step 8: Commit Agent evidence integration.**

```powershell
git add src/data_analysis_agent/execution src/data_analysis_agent/agent tests/agent
git commit -m "feat: connect agent results to evidence chain"
```

### Task 5: Documentation, full verification, and final review

**Files:**
- Modify: `README.md`
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

**Interfaces:**
- Documentation describes `EvidenceRegistry`, provenance requirements, verification states, pending-confirmation semantics, and the fact/interpretation distinction.
- No M11 report renderer changes and no database migration are introduced.

- [ ] **Step 1: Write documentation acceptance checks before editing README.**

Add a documentation test only if the repository’s existing test style already asserts README text; otherwise use the following `rg` checks as the executable acceptance contract:

```powershell
rg -n "EvidenceRegistry|VERIFIED|PENDING_CONFIRMATION|source_dataset_ids|code_hash|待确认|事实|解释" README.md
```

Expected: before the edit at least one required term is absent, establishing that the documentation change is meaningful.

- [ ] **Step 2: Add a concise M10 README section.**

Document one complete example using `MetricArtifact` provenance and `EvidenceRegistry.verify_metric`, state that the Agent never infers metrics from arbitrary stdout, and state that unsupported report numbers remain visible only with `PENDING_CONFIRMATION`. Explicitly say M10 is in-memory and does not add database tables.

- [ ] **Step 3: Run all focused and project tests.**

Run:

```powershell
pytest tests/domain/test_evidence_models.py tests/services/test_evidence_registry.py tests/agent/test_evidence_integration.py -q
pytest -q
python -m compileall -q src
git diff --check
```

Expected: focused tests pass; the full suite has zero failures; compileall exits 0; `git diff --check` prints no whitespace errors. Any pre-existing failure must be reproduced, diagnosed, and either fixed with a regression test or recorded in `progress.md` before completion is claimed.

- [ ] **Step 4: Perform the final static contract review.**

Run:

```powershell
rg -n "UNSUPPORTED_NUMERIC_CLAIM|PENDING_CONFIRMATION|source_uri|absolute path" src/data_analysis_agent/domain src/data_analysis_agent/services src/data_analysis_agent/agent README.md
git status --short
git log -8 --oneline --decorate
```

Verify manually that no evidence error exposes an absolute path, no unverified metric is included in the final prompt, no chart outside the task root is report-eligible, and no new database or M11 renderer file was changed.

- [ ] **Step 5: Mark the persistent plan complete and commit documentation/review notes.**

Update `task_plan.md` M10 items to `[x]`, append the final test counts and commit IDs to `progress.md`, record any resolved errors in `findings.md`, then run:

```powershell
git add README.md task_plan.md findings.md progress.md
git commit -m "docs: document M10 evidence chain"
```

The ignored planning files may be updated for continuity; only tracked documentation is committed.

## Self-review checklist

- [x] Every design requirement maps to a task: provenance models (Task 1), registration and recomputation (Task 2), chart checks and report numbers (Task 3), Agent integration (Task 4), and acceptance/documentation (Task 5).
- [x] The plan preserves the existing compatibility dictionary and explicitly excludes database migrations and M11 rendering.
- [x] Each task has a concrete RED test command, a minimal implementation boundary, a GREEN command, and a commit command.
- [x] The plan contains no unfinished instruction.
- [x] All public names used by later tasks are defined before use: evidence enums/models in Task 1, registry methods in Tasks 2-3, and Agent methods in Task 4.
