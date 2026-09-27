from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import inspect
import math
from time import monotonic
from typing import Any, Callable
from uuid import UUID

from ..agent.orchestration_errors import AgentOrchestrationError
from ..domain.enums import TaskStatus
from ..domain.models import AnalysisTask, TaskEvent, utc_now
from ..llm.errors import LLMError
from ..services.errors import sanitize_exception
from ..services.persistence import TaskPersistenceService
from .broker import TaskBroker
from .errors import RetryableTaskError, TaskEnqueueError
from .models import WorkerResult
from .service import CancellationRegistry, TaskSubmissionService


AgentFactory = Callable[..., Any]


class AnalysisTaskWorker:
    """Execute one persisted analysis task per broker delivery."""

    def __init__(
        self,
        *,
        persistence: TaskPersistenceService,
        broker: TaskBroker,
        agent_factory: AgentFactory,
        cancellation_registry: CancellationRegistry | None = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 5.0,
        stale_after_seconds: int = 1800,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be finite and non-negative")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.persistence = persistence
        self.broker = broker
        self.agent_factory = agent_factory
        self.cancellation_registry = cancellation_registry or CancellationRegistry()
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.stale_after_seconds = stale_after_seconds
        self._phase_started_at: dict[UUID, datetime] = {}

    def process(self, task_id: UUID) -> WorkerResult:
        claimed = self.persistence.claim_task(task_id)
        task = claimed.task
        if not claimed.claimed:
            return WorkerResult(
                task_id=task_id,
                status=task.status,
                executed=False,
                attempt=self._attempt(task),
            )

        started = monotonic()
        agent: Any | None = None
        self._phase_started_at[task_id] = task.updated_at
        self.cancellation_registry.register(task_id)
        try:
            if self.cancellation_registry.is_cancelled(task_id):
                cancelled = self.persistence.cancel_task(task_id)
                return self._result(
                    cancelled,
                    executed=False,
                    attempt=self._attempt(task),
                    started=started,
                )

            agent = self._create_agent(task)
            self.cancellation_registry.bind(
                task_id, lambda: self._stop_agent(agent)
            )
            if self.cancellation_registry.is_cancelled(task_id):
                cancelled = self.persistence.cancel_task(task_id)
                return self._result(
                    cancelled,
                    executed=False,
                    attempt=self._attempt(task),
                    started=started,
                )

            try:
                raw_result = self._run_agent(agent, task)
            except Exception as exc:
                if self.cancellation_registry.is_cancelled(task_id):
                    cancelled = self.persistence.cancel_task(task_id)
                    return self._result(
                        cancelled,
                        executed=True,
                        attempt=self._attempt(task),
                        started=started,
                    )
                return self._handle_failure(
                    task,
                    exc,
                    executed=True,
                    started=started,
                )

            if self.cancellation_registry.is_cancelled(task_id):
                cancelled = self.persistence.cancel_task(task_id)
                return self._result(
                    cancelled,
                    executed=True,
                    attempt=self._attempt(task),
                    started=started,
                )

            status, code, message, retryable = self._result_details(raw_result)
            if status is TaskStatus.CANCELLED:
                cancelled = self.persistence.cancel_task(task_id)
                return self._result(
                    cancelled,
                    executed=True,
                    attempt=self._attempt(task),
                    started=started,
                )
            if status is TaskStatus.FAILED:
                return self._handle_failure(
                    task,
                    RuntimeError(message or "analysis failed"),
                    code=code or "ANALYSIS_FAILED",
                    retryable=retryable,
                    executed=True,
                    started=started,
                )

            completed = self._complete_task(task_id)
            return self._result(
                completed,
                executed=True,
                attempt=self._attempt(completed),
                started=started,
            )
        finally:
            self.cancellation_registry.clear(task_id)
            self._phase_started_at.pop(task_id, None)
            self._close_agent(agent)

    def cancel(self, task_id: UUID):
        submission = TaskSubmissionService(
            persistence=self.persistence,
            broker=self.broker,
            cancellation_registry=self.cancellation_registry,
        )
        return submission.cancel(task_id)

    def recover_stale(self) -> list[UUID]:
        before = utc_now() - timedelta(seconds=self.stale_after_seconds)
        recovered = self.persistence.recover_stale_tasks(before=before)
        task_ids: list[UUID] = []
        for task in recovered:
            try:
                self.broker.enqueue(task.task_id)
            except Exception:
                self.persistence.fail_task(
                    task.task_id,
                    code="TASK_REQUEUE_FAILED",
                    message="recovered task could not be queued",
                )
                continue
            task_ids.append(task.task_id)
        return task_ids

    def _create_agent(self, task: AnalysisTask) -> Any:
        owner_id = self.persistence.get_task_owner_id(task.task_id)
        callback = self._on_agent_transition
        factory = self.agent_factory
        candidates = (
            {"task": task, "user_id": owner_id, "on_transition": callback},
            {"task": task, "user_id": owner_id},
            {"task": task, "on_transition": callback},
            {"task": task},
        )
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            return factory(task)
        for kwargs in candidates:
            try:
                signature.bind(**kwargs)
            except TypeError:
                continue
            return factory(**kwargs)
        return factory(task)

    @staticmethod
    def _run_agent(agent: Any, task: AnalysisTask) -> Any:
        run = getattr(agent, "run", None)
        if callable(run):
            return run()
        analyze = getattr(agent, "analyze", None)
        if callable(analyze):
            try:
                signature = inspect.signature(analyze)
                try:
                    signature.bind(task.query, dataset_ids=task.dataset_ids)
                except TypeError:
                    return analyze(task.query)
            except (TypeError, ValueError):
                pass
            return analyze(task.query, dataset_ids=task.dataset_ids)
        if callable(agent):
            return agent(task)
        raise TypeError("agent factory returned a non-callable agent")

    def _handle_failure(
        self,
        task: AnalysisTask,
        exception: BaseException,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        executed: bool,
        started: float,
    ) -> WorkerResult:
        retryable = self._is_retryable(exception) if retryable is None else retryable
        attempt = self._attempt(task)
        error_code = (
            code
            or getattr(exception, "cause_code", None)
            or getattr(exception, "code", None)
            or "WORKER_FAILED"
        )
        error_message = self._safe_message(exception)
        if retryable and attempt < self.max_retries:
            queued = self.persistence.requeue_for_retry(
                task.task_id,
                code=error_code,
                message=error_message,
            )
            countdown = self.retry_backoff_seconds * (2**attempt)
            try:
                self.broker.enqueue(task.task_id, countdown=countdown)
            except Exception as enqueue_error:
                failed = self.persistence.fail_task(
                    task.task_id,
                    code="TASK_REQUEUE_FAILED",
                    message="task retry could not be queued",
                )
                return self._result(
                    failed,
                    executed=executed,
                    attempt=self._attempt(failed),
                    started=started,
                    error_code="TASK_REQUEUE_FAILED",
                    error_message="task retry could not be queued",
                )
            return self._result(
                queued,
                executed=executed,
                attempt=self._attempt(queued),
                started=started,
                retry_scheduled=True,
                error_code=error_code,
                error_message=error_message,
            )

        failure_code = "WORKER_RETRY_EXHAUSTED" if retryable else error_code
        failed = self.persistence.fail_task(
            task.task_id,
            code=failure_code,
            message=error_message,
        )
        return self._result(
            failed,
            executed=executed,
            attempt=self._attempt(failed),
            started=started,
            error_code=failure_code,
            error_message=error_message,
        )

    def _complete_task(self, task_id: UUID) -> AnalysisTask:
        current = self.persistence.get_task(task_id)
        if current is None:
            raise TaskEnqueueError("task disappeared while executing")
        for target in (
            TaskStatus.EXPLORING,
            TaskStatus.CLEANING,
            TaskStatus.ANALYZING,
            TaskStatus.VALIDATING,
            TaskStatus.REPORTING,
            TaskStatus.COMPLETED,
        ):
            phase_finished_at = utc_now()
            phase_started_at = self._phase_started_at.get(
                current.status, current.updated_at
            )
            current = self.persistence.record_stage_transition(
                task_id=task_id,
                target=target,
                message=f"worker completed {target.value.lower()} stage",
                occurred_at=phase_finished_at,
                metadata=self._phase_metadata(
                    current.status,
                    target,
                    started_at=phase_started_at,
                    finished_at=phase_finished_at,
                ),
            )
            self._phase_started_at[target] = phase_finished_at
            if current.status in {
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                break
        return current

    def _on_agent_transition(
        self, task: AnalysisTask, event: TaskEvent
    ) -> None:
        if task.task_id is None:
            return
        target = event.to_status
        if target in {TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return
        phase_finished_at = event.occurred_at
        phase_started_at = self._phase_started_at.get(
            event.from_status, phase_finished_at
        )
        self.persistence.record_stage_transition(
            task_id=task.task_id,
            target=target,
            message=event.message,
            occurred_at=event.occurred_at,
            metadata=self._phase_metadata(
                event.from_status,
                target,
                started_at=phase_started_at,
                finished_at=phase_finished_at,
            ),
        )
        self._phase_started_at[target] = event.occurred_at

    @staticmethod
    def _phase_metadata(
        previous: TaskStatus | None,
        target: TaskStatus,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> dict[str, Any]:
        finished = finished_at or utc_now()
        started = started_at or finished
        metadata: dict[str, Any] = {
            "phase_started_at": started.isoformat(),
            "phase_finished_at": finished.isoformat(),
        }
        if previous is not None:
            metadata["completed_phase"] = previous.value
        return metadata

    @staticmethod
    def _result_details(
        result: Any,
    ) -> tuple[TaskStatus, str | None, str | None, bool]:
        if result is None:
            return TaskStatus.COMPLETED, None, None, False
        if isinstance(result, Mapping):
            raw_status = result.get("status", TaskStatus.COMPLETED)
            code = result.get("error_code")
            message = result.get("error_message") or result.get("error")
            retryable = bool(result.get("retryable", False))
        else:
            raw_status = getattr(result, "status", TaskStatus.COMPLETED)
            code = getattr(result, "error_code", None)
            message = getattr(result, "error_message", None)
            retryable = bool(getattr(result, "retryable", False))
        try:
            status = raw_status if isinstance(raw_status, TaskStatus) else TaskStatus(raw_status)
        except (TypeError, ValueError):
            status = TaskStatus.COMPLETED
        return status, code, message, retryable

    @staticmethod
    def _is_retryable(exception: BaseException) -> bool:
        if isinstance(exception, RetryableTaskError):
            return True
        if isinstance(exception, LLMError):
            return bool(getattr(exception, "retryable", False))
        if isinstance(exception, AgentOrchestrationError):
            return bool(getattr(exception, "retryable", False))
        return bool(getattr(exception, "retryable", False))

    @staticmethod
    def _safe_message(exception: BaseException) -> str:
        return sanitize_exception(exception)[:1000]

    @staticmethod
    def _attempt(task: AnalysisTask) -> int:
        value = task.metadata.get("worker_attempt", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _result(
        task: AnalysisTask,
        *,
        executed: bool,
        attempt: int,
        started: float,
        retry_scheduled: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id,
            status=task.status,
            executed=executed,
            retry_scheduled=retry_scheduled,
            attempt=attempt,
            error_code=error_code or task.error_code,
            error_message=error_message or task.error_message,
            duration_seconds=max(0.0, monotonic() - started),
        )

    @staticmethod
    def _stop_agent(agent: Any) -> None:
        if agent is None:
            return
        direct_cancel = getattr(agent, "cancel", None)
        if callable(direct_cancel):
            try:
                direct_cancel()
            except Exception:
                pass
            return
        for target in (
            getattr(agent, "orchestrator", None),
            getattr(agent, "executor", None),
        ):
            for name in ("cancel", "stop", "terminate", "kill"):
                callback = getattr(target, name, None)
                if callable(callback):
                    try:
                        callback()
                    except Exception:
                        pass

    @staticmethod
    def _close_agent(agent: Any) -> None:
        if agent is None:
            return
        close = getattr(agent, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


__all__ = ["AnalysisTaskWorker"]
