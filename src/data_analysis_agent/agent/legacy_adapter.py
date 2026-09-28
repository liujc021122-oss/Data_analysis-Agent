from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from enum import Enum
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from ..domain.enums import TaskStatus
from ..domain.models import AnalysisTask
from ..llm import LLMStructuredOutputError
from .orchestration_models import (
    OrchestrationResult,
    OrchestratorLimits,
    StageFailure,
    StageInput,
    StageResult,
)
from .prompts import data_analysis_system_prompt


class LegacyAnalysisAdapter:
    """Map the legacy ``DataAnalysisAgent`` loop onto orchestration stages."""

    def __init__(self, *, agent, user_input, dataset_context, max_rounds):
        self.agent = agent
        self.user_input = user_input
        self.dataset_context = tuple(dataset_context)
        self.max_rounds = max_rounds
        self.task = AnalysisTask(
            query=user_input,
            dataset_ids=tuple(
                UUID(item["dataset_id"])
                for item in self.dataset_context
                if item.get("dataset_id")
            ),
            max_rounds=max(1, max_rounds),
        )
        self.limits = OrchestratorLimits(
            max_steps=max(6, max_rounds + 6),
            max_model_calls=max(1, max_rounds) + 1,
        )
        self._report_called = False
        self._raw_report_output: dict[str, Any] | None = None
        self._report_output: dict[str, Any] = {}
        self.report_exception: BaseException | None = None

    def handlers(self):
        return {
            TaskStatus.RUNNING: self._initialize,
            TaskStatus.EXPLORING: self._explore,
            TaskStatus.CLEANING: self._clean,
            TaskStatus.ANALYZING: self._analyze_step,
            TaskStatus.VALIDATING: self._validate,
            TaskStatus.REPORTING: self._report,
        }

    def _initialize(self, stage_input: StageInput, call_tool) -> StageResult:
        return self._context_stage_result()

    def _explore(self, stage_input: StageInput, call_tool) -> StageResult:
        return self._context_stage_result()

    def _clean(self, stage_input: StageInput, call_tool) -> StageResult:
        return self._context_stage_result()

    def _validate(self, stage_input: StageInput, call_tool) -> StageResult:
        return self._context_stage_result()

    def _context_stage_result(self) -> StageResult:
        if getattr(self.agent, "validation_error", None):
            return StageResult(
                completed=False,
                failure=StageFailure(
                    code="VALIDATION_ERROR",
                    message="analysis context validation failed",
                ),
            )
        return StageResult()

    def _analyze_step(self, stage_input: StageInput, call_tool) -> StageResult:
        if self.agent.current_round >= self.max_rounds:
            return StageResult()

        self.agent.current_round += 1
        try:
            model_action = self._request_action()
            response = model_action.model_dump_json()
        except _ModelActionError as error:
            return _model_action_failure(error)
        except Exception:
            return _model_action_failure(_ModelActionError("MODEL_ERROR"))
        process_result = self.agent._process_action(model_action, response)

        if not process_result.get("continue", True):
            return StageResult(model_calls=1)

        self.agent.conversation_history.append(
            {"role": "assistant", "content": response}
        )
        action = process_result.get("action")
        if action == "generate_code":
            self._record_generate_code(process_result, response)
        elif action == "collect_figures":
            self._record_collected_figures(process_result, response)

        completed = self.agent.current_round >= self.max_rounds
        return StageResult(completed=completed, model_calls=1)

    def _request_action(self):
        executor = getattr(self.agent, "executor", None)
        get_environment_info = getattr(executor, "get_environment_info", None)
        notebook_variables = (
            get_environment_info() if callable(get_environment_info) else ""
        )
        formatted_system_prompt = data_analysis_system_prompt.format(
            notebook_variables=notebook_variables
        )
        try:
            return self.agent._request_structured_action(
                prompt=self.agent._build_conversation_prompt(),
                system_prompt=formatted_system_prompt,
            )
        except (LLMStructuredOutputError, ValidationError) as exc:
            raise _ModelActionError("MODEL_SCHEMA_ERROR") from exc
        except Exception as exc:
            raise _ModelActionError("MODEL_ERROR") from exc

    def _record_generate_code(self, process_result: Mapping[str, Any], response: str) -> None:
        feedback = process_result.get("feedback", "")
        if self.agent.analysis_results:
            previous = self.agent.analysis_results[-1]
            previous_result = previous.get("result", {})
            if isinstance(previous_result, Mapping) and not previous_result.get("success", True):
                feedback = (
                    f"{feedback}\n上一次执行失败代码:\n"
                    f"{previous.get('code', '')}"
                )
        self.agent.conversation_history.append(
            {"role": "user", "content": f"代码执行反馈:\n{feedback}"}
        )
        self.agent.analysis_results.append(
            {
                "round": self.agent.current_round,
                "code": process_result.get("code", ""),
                "result": process_result.get("result", {}),
                "response": response,
            }
        )

    def _record_collected_figures(
        self, process_result: Mapping[str, Any], response: str
    ) -> None:
        collected_figures = process_result.get("collected_figures", [])
        feedback = f"已收集 {len(collected_figures)} 个图片及其分析"
        self.agent.conversation_history.append(
            {
                "role": "user",
                "content": f"图片收集反馈:\n{feedback}\n请继续下一步分析。",
            }
        )
        self.agent.analysis_results.append(
            {
                "round": self.agent.current_round,
                "action": "collect_figures",
                "collected_figures": collected_figures,
                "response": response,
            }
        )

    def _report(self, stage_input: StageInput, call_tool) -> StageResult:
        if self._report_called:
            return StageResult(output=self._report_output)

        self._report_called = True
        try:
            report_output = self.agent._generate_final_report()
        except Exception as exc:
            # Keep the original exception on the compatibility adapter.  The
            # orchestrator intentionally exposes only a sanitized terminal
            # result; the legacy facade can then preserve its historical
            # exception behavior without leaking the exception through the
            # orchestration event payload.
            self.report_exception = exc
            raise
        if not isinstance(report_output, Mapping):
            return StageResult(
                completed=False,
                model_calls=1,
                failure=StageFailure(
                    code="REPORT_OUTPUT_INVALID",
                    message="final report output must be a mapping",
                ),
            )

        self._raw_report_output = dict(report_output)
        self._report_output = self._json_safe(report_output)
        return StageResult(output=self._report_output, model_calls=1)

    def to_legacy_result(
        self,
        result: OrchestrationResult,
        *,
        json_safe: bool = True,
    ) -> dict[str, Any]:
        """Return the established result dictionary used by the legacy facade."""
        report_output = dict(self._raw_report_output or result.output)
        analysis_results = getattr(self.agent, "analysis_results", [])
        conversation_history = getattr(self.agent, "conversation_history", [])
        all_figures = self._collect_figures(analysis_results)

        registry = getattr(self.agent, "evidence_registry", None)
        if registry is not None:
            snapshot = registry.snapshot(
                getattr(self.agent, "task_id", result.task_id)
            )
            report_output.setdefault(
                "metric_artifacts",
                [item.model_dump(mode="json") for item in snapshot["metrics"]],
            )
            report_output.setdefault(
                "chart_artifacts",
                [item.model_dump(mode="json") for item in snapshot["charts"]],
            )
            report_output.setdefault(
                "evidence_claims",
                [item.model_dump(mode="json") for item in snapshot["claims"]],
            )
            validation = snapshot.get("validation")
            if validation is not None:
                report_output.setdefault(
                    "evidence_validation",
                    validation.model_dump(mode="json"),
                )

        report_output.update(
            {
                "session_output_dir": report_output.get(
                    "session_output_dir", getattr(self.agent, "session_output_dir", None)
                ),
                "total_rounds": getattr(self.agent, "current_round", 0),
                "analysis_results": analysis_results,
                "collected_figures": report_output.get("collected_figures", all_figures),
                "conversation_history": conversation_history,
                "task_id": report_output.get(
                    "task_id", getattr(self.agent, "task_id", result.task_id)
                ),
                "artifact_records": report_output.get(
                    "artifact_records", getattr(self.agent, "artifact_records", [])
                ),
                "execution_audits": report_output.get(
                    "execution_audits", getattr(self.agent, "execution_audits", [])
                ),
                "report_download_url": report_output.get("report_download_url"),
                "report_content_url": report_output.get("report_content_url"),
                "html_report_download_url": report_output.get(
                    "html_report_download_url"
                ),
                "html_report_content_url": report_output.get(
                    "html_report_content_url"
                ),
                "word_report_download_url": report_output.get(
                    "word_report_download_url"
                ),
                "word_report_content_url": report_output.get(
                    "word_report_content_url"
                ),
                "storage_error": report_output.get(
                    "storage_error", getattr(self.agent, "storage_error", None)
                ),
            }
        )
        return self._json_safe(report_output) if json_safe else report_output

    @staticmethod
    def _collect_figures(analysis_results: Sequence[Any]) -> list[Any]:
        figures: list[Any] = []
        for result in analysis_results:
            if isinstance(result, Mapping) and result.get("action") == "collect_figures":
                figures.extend(result.get("collected_figures", ()))
        return figures

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return cls._json_safe(value.model_dump(mode="json"))
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_safe(item) for item in value]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if value == value and abs(value) != float("inf") else str(value)
        if isinstance(value, (UUID, datetime, date, time, Path)):
            return str(value)
        if isinstance(value, Enum):
            return cls._json_safe(value.value)
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            return str(value)
        return value


class _ModelActionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _model_action_failure(error: _ModelActionError) -> StageResult:
    return StageResult(
        completed=False,
        model_calls=1,
        failure=StageFailure(
            code=error.code,
            message="structured model action failed",
        ),
    )


__all__ = ["LegacyAnalysisAdapter"]
