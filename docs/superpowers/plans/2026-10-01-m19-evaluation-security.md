# M19 Evaluation And Security Testing Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Goal: Add a deterministic, offline evaluation suite that measures Agent quality and exercises data, model, sandbox, report, isolation, and authorization security boundaries.

Architecture: Add a small data_analysis_agent.evaluation package containing validated evaluation models and a runner driven by an injected offline adapter. Keep fixed datasets and scenario adapters in tests/fixtures/m19 and tests/evaluation; drive existing upload, LLM, execution, evidence, and authorization boundaries instead of duplicating them.

Tech Stack: Python 3.10+, Pydantic 2, pytest 9, existing FakeLLM/FakeRuntime fixtures, pandas, matplotlib, EvidenceRegistry, ReportService, ToolExecutor, and AuthorizationService.

## Global Constraints

- CI evaluation tests must not require a real LLM API, network access, Docker, Redis, or object storage.
- Preserve existing M09/M10/M17 security behavior and public compatibility contracts.
- Evaluation models may contain only JSON-safe scalar, array, and object values.
- Do not persist prompts, complete model responses, source code, API keys, host absolute paths, or raw sensitive dataset values in evaluation results.
- Safety failures must be fail-closed and use stable error codes already defined by the bounded module.
- Use E:\anaconda\Scripts\pytest.exe for verification in this workspace.
- Run git diff --check before each task commit and do not stage the existing untracked worktrees/ directory.

## File Map

- Create src/data_analysis_agent/evaluation/models.py for case, observation, metric, threshold, and report models.
- Create src/data_analysis_agent/evaluation/runner.py for the adapter protocol and deterministic report runner.
- Create src/data_analysis_agent/evaluation/__init__.py and modify src/data_analysis_agent/__init__.py for public imports.
- Create tests/fixtures/m19/store_sales.csv and tests/fixtures/m19/store_sales_expected.json.
- Create tests/evaluation/conftest.py and tests/evaluation/support.py for fixed paths and the offline Agent adapter.
- Create tests/evaluation/test_models.py, test_runner.py, test_store_sales.py, test_data_validation.py, test_model_protocol.py, test_execution_security.py, test_report_consistency.py, and test_isolation_authorization.py.
- Modify README.md with the offline M19 command and metric definitions.
- Create or update sdd/m19-task-plan.md, sdd/m19-findings.md, and sdd/m19-progress.md; do not alter the existing root M09 planning files.

---

### Task 1: Evaluation Models And Runner

Files:
- Create src/data_analysis_agent/evaluation/models.py
- Create src/data_analysis_agent/evaluation/runner.py
- Create src/data_analysis_agent/evaluation/__init__.py
- Modify src/data_analysis_agent/__init__.py
- Test tests/evaluation/test_models.py and tests/evaluation/test_runner.py

Interfaces:
- EvaluationCategory(str, Enum) values: QUALITY, DATA_VALIDATION, MODEL_PROTOCOL, SANDBOX, REPORT, ISOLATION, AUTHORIZATION.
- EvaluationCase fields: case_id, category, description, optional query and dataset_name, JSON-safe expected, and quality_gate.
- EvaluationObservation fields: passed, task_completed, first_execution_succeeded, retry_count, metric_accuracy, report_numeric_consistency, chart_generated, duration_ms, and token_cost_usd.
- EvaluationCaseResult contains the case, optional observation, passed, error_code, and safe failure_summary.
- EvaluationThresholds has minimum values for the five quality rates, a maximum retry count, and optional duration/cost maxima.
- EvaluationMetrics.from_results(results) calculates the eight metrics only from quality_gate=True cases.
- EvaluationAdapter.evaluate(case) returns EvaluationObservation.
- EvaluationRunner(adapter).run(cases, thresholds=None, run_id=None) sorts by case_id, converts adapter/protocol failures to ADAPTER_FAILURE, computes metrics, and checks thresholds.
- EvaluationReport.to_json() returns JSON-safe Pydantic output.

- [ ] Step 1: Write failing tests.

Test invalid non-JSON expected values, metric aggregation from one quality case while excluding one security case, stable case ordering, threshold failure for retries, and conversion of an adapter exception to ADAPTER_FAILURE with failure_summary equal to evaluation adapter failed. Assert exception text, paths, and secrets are absent from report.to_json().

- [ ] Step 2: Verify RED.

Run:

```powershell
E:\anaconda\Scripts\pytest.exe tests/evaluation/test_models.py tests/evaluation/test_runner.py -q
```

Expected: collection fails because data_analysis_agent.evaluation does not exist.

- [ ] Step 3: Implement the models.

Use Pydantic BaseModel with extra=forbid. Enforce nonblank case IDs/descriptions, accuracy values in [0, 1], nonnegative retry/duration/cost values, timezone-aware report timestamps, and a recursive JSON-safe validator for expected. Define the eight metric fields and threshold fields with the formulas from the design document.

- [ ] Step 4: Implement the runner and exports.

Sort cases, call the adapter, validate its returned observation, and catch exceptions or invalid observations with the fixed safe failure result. Calculate quality-only metrics, evaluate all non-None thresholds, set report passed only when every case and threshold pass, and re-export the public types from both package init files.

- [ ] Step 5: Verify GREEN.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_models.py tests/evaluation/test_runner.py -q. Expected: all focused tests pass.

- [ ] Step 6: Commit.

Run git diff --check and stage only the evaluation package, root exports, and focused tests. Commit with message feat: add offline evaluation models and runner.

---

### Task 2: Fixed Store Sales Quality Case

Files:
- Create tests/fixtures/m19/store_sales.csv
- Create tests/fixtures/m19/store_sales_expected.json
- Create tests/evaluation/conftest.py
- Create tests/evaluation/support.py
- Create tests/evaluation/test_store_sales.py

Interfaces:
- CSV columns are store,date,sales. Expected totals are A=250, B=350, C=260, D=230; top three are ["B", "C", "A"]; total is 1090; chart type is bar.
- store_sales_case(path) returns case ID quality.store-sales, category QUALITY, the fixed Chinese query, expected JSON values, and quality_gate=True.
- StoreSalesAdapter.evaluate(case) runs DataAnalysisAgent with FakeLLM, injected EvidenceRegistry, a temporary output root, and generate_word_report=False.
- The adapter parses JSON printed by generated code, checks the verified chart artifact, checks report numbers/evidence validation, and asserts the source path is absent from every FakeLLM prompt.

- [ ] Step 1: Write the failing fixed-case test and fixtures.

Create the CSV and expected JSON with the exact values above. Add a test that runs EvaluationRunner(StoreSalesAdapter(...)) on store_sales_case and asserts report passed, task completion rate, metric accuracy, report numeric consistency, and chart success are all 1.0.

- [ ] Step 2: Verify RED.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_store_sales.py::test_store_sales_case_completes_with_correct_totals_chart_and_report -q. Expected: setup fails because the fixture path and StoreSalesAdapter do not exist.

- [ ] Step 3: Implement the offline adapter.

Reuse FakeLLM, yaml_response, dataset_id_from_prompt, and session_dir_from_prompt. Generated code loads load_dataset(dataset_ids[0]), groups sales by store, prints JSON with store_totals, top_3, and total_sales, and writes store_sales_top3.png under session_output_dir. The collect-figures response points to that file. Pre-register and verify one MetricArtifact per expected total so the existing evidence path validates the final report.

- [ ] Step 4: Verify GREEN.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_store_sales.py -q. Expected: the fixed case passes with no API key, network, or Docker runtime.

- [ ] Step 5: Commit.

Run git diff --check, stage the M19 fixture and quality test files, and commit with message test: add fixed offline store sales evaluation.

---

### Task 3: Data And Model Protocol Regression Matrix

Files:
- Create tests/evaluation/test_data_validation.py
- Create tests/evaluation/test_model_protocol.py

Interfaces:
- Data tests call CsvInspector.inspect and DatasetUploadService.upload and assert DatasetErrorCode plus storage/metadata cleanup.
- Model tests call LLMClient.astructured_output with the existing Fake Provider and structured Answer schema, asserting exactly two structured attempts and stable final errors.

- [ ] Step 1: Add data contract tests.

Cover empty payload as EMPTY_FILE, duplicate headers as DUPLICATE_COLUMNS, missing values as a valid profile with missing_count=1 and missing_rate=0.5, invalid bytes as ENCODING_DETECTION_FAILED, oversized upload as FILE_TOO_LARGE with no object or metadata residue, and data.exe as UNSUPPORTED_EXTENSION.

- [ ] Step 2: Run the data tests.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_data_validation.py -q. Expected: existing validation behavior is green. If a test exposes a real gap, keep the assertion as RED, record the exact failure in sdd/m19-task-plan.md, and make only the smallest bounded validation fix in a separate RED/GREEN commit.

- [ ] Step 3: Add model protocol tests.

Replay not json followed by wrong-shape JSON and assert LLMStructuredOutputError.attempts == 2 and two provider calls. Replay two LLMTimeoutError values and assert the stable timeout exception with no execution. Assert correction prompts contain schema field names but no provider payload, secret, or arbitrary model instructions.

- [ ] Step 4: Run and commit.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_data_validation.py tests/evaluation/test_model_protocol.py -q and git diff --check. Commit with message test: cover evaluation data and model failure cases.

---

### Task 4: Sandbox And Tool Security Regression Matrix

Files:
- Create tests/evaluation/test_execution_security.py

Interfaces:
- Reuse FakeRuntime and make_request patterns from tests/execution/test_container_executor.py.
- Reuse ToolExecutor, ToolRegistry, ToolDefinition, ToolContext, and ToolCallRequest with a typed test tool.
- Assert stable ExecutionErrorCode and tool error codes, not raw exception text.

- [ ] Step 1: Add security contract tests.

Cover a timed-out container and assert kill < wait < remove order and TIMEOUT; an input path outside source_scope and assert PATH_TRAVERSAL with no outside copy; output and resource limits; default container network mode none; tool network denial when network_allowed=False; task-context mismatch; missing high-risk permission; and sanitized errors with no host paths or secrets.

- [ ] Step 2: Run the security tests.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_execution_security.py -q. Expected: existing M09 and tool policy behavior is green. If a failure appears, reproduce it against the focused existing test before changing production code.

- [ ] Step 3: Commit.

Run git diff --check, stage the security test, and commit with message test: add M19 sandbox and tool security cases.

---

### Task 5: Report, Concurrency, And Authorization Matrix

Files:
- Create tests/evaluation/test_report_consistency.py
- Create tests/evaluation/test_isolation_authorization.py

Interfaces:
- Report tests use EvidenceRegistry, MetricArtifact, ChartArtifact, ReportDocument, and ReportService.
- Isolation tests use ThreadPoolExecutor with two task IDs and output roots plus the existing AgentExecutionSession or injected backend.
- Authorization tests use AccessSubject and AuthorizationService and existing API owner/foreign/admin fixtures where an HTTP-level assertion is useful.

- [ ] Step 1: Add report consistency tests.

Register and verify a metric with value 350.0; generate a report containing 350 and unsupported 999; assert 350 remains, the pending-confirmation marker appears, and validation is false. Register a chart belonging to another task and assert the registry rejects its check; assert no foreign chart becomes report evidence.

- [ ] Step 2: Add concurrent isolation tests.

Run two independent sessions concurrently with a backend that writes a task-specific chart into its received output directory. Assert both succeed, task IDs differ, each output root contains only its own files, and neither result contains the other task ID or output path.

- [ ] Step 3: Add authorization tests.

Assert an owner can access its resource, a different non-admin cannot, an admin can access the foreign owner, and require_admin raises PermissionError for the non-admin without exposing resource identifiers.

- [ ] Step 4: Run and commit.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation/test_report_consistency.py tests/evaluation/test_isolation_authorization.py -q and git diff --check. Commit with message test: cover report consistency and task isolation.

---

### Task 6: Documentation And Full Verification

Files:
- Modify README.md
- Modify sdd/m19-task-plan.md
- Modify sdd/m19-findings.md
- Modify sdd/m19-progress.md

- [ ] Step 1: Document M19.

Add the fixed store-sales case, no-real-API guarantee, exact evaluation command, safety scenario groups, and all eight metric names/formulas. State that network/Docker cases use injected fakes and policy checks.

- [ ] Step 2: Run the dedicated suite.

Run E:\anaconda\Scripts\pytest.exe tests/evaluation -q. Expected: all M19 tests pass with no external service setup.

- [ ] Step 3: Run full offline verification.

Run E:\anaconda\Scripts\pytest.exe -q, E:\anaconda\python.exe -m compileall -q src, and git diff --check. Record actual pass/skip/warning counts. Any unrelated pre-existing failure must be recorded by exact test name and error; do not claim a clean suite.

- [ ] Step 4: Review scope and data leaks.

Confirm no fixture, report, or evaluation JSON contains API keys, host paths, arbitrary source code, or raw sensitive values. Confirm git status --short lists only intended M19 files plus pre-existing worktrees/.

- [ ] Step 5: Close the plan and commit documentation.

Update M19 plan statuses and progress with actual verification evidence, then stage README.md and the three M19 sdd files and commit with message docs: document M19 offline evaluation suite.

## Plan Self-Review

- Spec coverage: Task 1 implements public evaluation models, runner, thresholds, safe JSON, and exports; Task 2 implements the fixed store-sales case and quality metrics; Task 3 covers all data and model-protocol cases; Task 4 covers sandbox timeout, path, network, resource, and permission cases; Task 5 covers report numbers, charts, concurrency, and owner/admin isolation; Task 6 covers documentation and full verification.
- Scope: no online evaluation API, persistence, dashboard, real provider, network, or Docker dependency is introduced.
- Type consistency: later tasks consume only the evaluation names defined in Task 1 and existing module names.
- Placeholder scan: no unresolved implementation step remains in the plan.
