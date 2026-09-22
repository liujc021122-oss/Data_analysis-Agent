"""Policy-enforced execution for registered tools."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from functools import partial
import inspect
import logging
import time
from typing import Any

from pydantic import ValidationError

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.audit import ToolAuditRecord, ToolCallRecorder
from data_analysis_agent.tools.errors import ToolError, UnknownToolError
from data_analysis_agent.tools.models import (
    ToolCallRequest,
    ToolCallResult,
    ToolContext,
    ToolRiskLevel,
)
from data_analysis_agent.tools.registry import ToolRegistry


_LOGGER = logging.getLogger(__name__)
_SAFE_ERROR_MESSAGES = {
    "UNKNOWN_TOOL": "The requested tool is unavailable",
    "TOOL_CONTEXT_INVALID": "Tool context is invalid",
    "TOOL_INPUT_INVALID": "Tool input is invalid",
    "TOOL_PERMISSION_DENIED": "Tool permission denied",
    "TOOL_NETWORK_DENIED": "Tool network access denied",
    "TOOL_TIMEOUT": "Tool execution timed out",
    "TOOL_OUTPUT_INVALID": "Tool output is invalid",
    "TOOL_DEPENDENCY_FAILED": "Tool dependency failed",
    "TOOL_AUDIT_PERSISTENCE_FAILED": "Tool audit persistence failed",
    "TOOL_EXECUTION_FAILED": "Tool execution failed",
}
_SYNC_EXECUTOR: ContextVar[ThreadPoolExecutor | None] = ContextVar(
    "tool_executor_sync_executor", default=None
)


class _TimeoutFriendlyThreadPoolExecutor(ThreadPoolExecutor):
    """Do not delay a timeout result for a non-cancellable worker thread."""

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        super().shutdown(wait=False, cancel_futures=cancel_futures)


class ToolExecutor:
    """Execute registry tools after validating context, policy, and schemas."""

    def __init__(self, registry: ToolRegistry, recorder: ToolCallRecorder | None = None) -> None:
        self._registry = registry
        self._recorder = recorder

    @property
    def registry(self) -> ToolRegistry:
        """The executor's registry; exposed without a setter."""
        return self._registry

    def execute(self, request: ToolCallRequest, context: ToolContext) -> ToolCallResult:
        """Execute a request for ordinary synchronous callers."""
        executor = _TimeoutFriendlyThreadPoolExecutor()
        token = _SYNC_EXECUTOR.set(executor)
        try:
            return asyncio.run(self.aexecute(request, context))
        finally:
            _SYNC_EXECUTOR.reset(token)
            executor.shutdown(wait=False, cancel_futures=True)

    async def aexecute(self, request: ToolCallRequest, context: ToolContext) -> ToolCallResult:
        """Execute a request while enforcing its registered tool policy."""
        sync_executor = _SYNC_EXECUTOR.get()
        owns_executor = sync_executor is None
        executor_token = None
        if owns_executor:
            sync_executor = _TimeoutFriendlyThreadPoolExecutor()
            executor_token = _SYNC_EXECUTOR.set(sync_executor)
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()

        def failed(code: str) -> ToolCallResult:
            return self._result(
                request,
                started_at,
                started_monotonic,
                status=ToolCallStatus.FAILED,
                error_code=code,
                error_message=_SAFE_ERROR_MESSAGES[code],
            )

        try:
            try:
                registered = self._registry.get(request.tool_name, request.task_id)
            except UnknownToolError:
                result = failed("UNKNOWN_TOOL")
                self._record(request, result)
                return result

            if request.task_id != context.task_id:
                result = failed("TOOL_CONTEXT_INVALID")
            else:
                try:
                    tool_input = registered.definition.input_model.model_validate(request.arguments)
                except ValidationError:
                    result = failed("TOOL_INPUT_INVALID")
                else:
                    definition = registered.definition
                    if not definition.required_permissions.issubset(context.permissions):
                        result = failed("TOOL_PERMISSION_DENIED")
                    elif definition.network_access and not context.network_allowed:
                        result = failed("TOOL_NETWORK_DENIED")
                    elif (
                        definition.risk_level is ToolRiskLevel.HIGH
                        and "execute:python" not in context.permissions
                    ):
                        result = failed("TOOL_PERMISSION_DENIED")
                    else:
                        result = await self._invoke(
                            request,
                            context,
                            registered.handler,
                            definition.output_model,
                            tool_input,
                            definition.max_runtime_seconds,
                            started_at,
                            started_monotonic,
                            sync_executor,
                        )

            self._record(request, result)
            return result
        finally:
            if owns_executor:
                assert executor_token is not None
                _SYNC_EXECUTOR.reset(executor_token)
                assert sync_executor is not None
                sync_executor.shutdown(wait=False, cancel_futures=True)

    async def _invoke(
        self,
        request: ToolCallRequest,
        context: ToolContext,
        handler: Any,
        output_model: Any,
        tool_input: Any,
        timeout_seconds: float,
        started_at: datetime,
        started_monotonic: float,
        sync_executor: ThreadPoolExecutor,
    ) -> ToolCallResult:
        try:
            handler_output = await asyncio.wait_for(
                self._call_handler(handler, tool_input, context, sync_executor),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return self._result(
                request,
                started_at,
                started_monotonic,
                status=ToolCallStatus.FAILED,
                error_code="TOOL_TIMEOUT",
                error_message=_SAFE_ERROR_MESSAGES["TOOL_TIMEOUT"],
            )
        except ToolError as exc:
            error_code = (
                exc.code
                if exc.code in _SAFE_ERROR_MESSAGES
                else "TOOL_EXECUTION_FAILED"
            )
            return self._result(
                request,
                started_at,
                started_monotonic,
                status=ToolCallStatus.FAILED,
                error_code=error_code,
                error_message=_SAFE_ERROR_MESSAGES[error_code],
            )
        except Exception:
            return self._result(
                request,
                started_at,
                started_monotonic,
                status=ToolCallStatus.FAILED,
                error_code="TOOL_EXECUTION_FAILED",
                error_message=_SAFE_ERROR_MESSAGES["TOOL_EXECUTION_FAILED"],
            )

        try:
            validated_output = output_model.model_validate(handler_output)
            output = validated_output.model_dump(mode="json")
        except Exception:
            return self._result(
                request,
                started_at,
                started_monotonic,
                status=ToolCallStatus.FAILED,
                error_code="TOOL_OUTPUT_INVALID",
                error_message=_SAFE_ERROR_MESSAGES["TOOL_OUTPUT_INVALID"],
            )

        return self._result(
            request,
            started_at,
            started_monotonic,
            status=ToolCallStatus.SUCCEEDED,
            output=output,
        )

    async def _call_handler(
        self,
        handler: Any,
        tool_input: Any,
        context: ToolContext,
        sync_executor: ThreadPoolExecutor,
    ) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler(tool_input, context)
        handler_call = partial(handler, tool_input, context)
        output = await asyncio.get_running_loop().run_in_executor(
            sync_executor,
            copy_context().run,
            handler_call,
        )
        if inspect.isawaitable(output):
            return await output
        return output

    def _result(
        self,
        request: ToolCallRequest,
        started_at: datetime,
        started_monotonic: float,
        *,
        status: ToolCallStatus,
        output: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ToolCallResult:
        finished_at = datetime.now(timezone.utc)
        duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        return ToolCallResult(
            call_id=request.call_id,
            task_id=request.task_id,
            tool_name=request.tool_name,
            status=status,
            output=output,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

    def _record(self, request: ToolCallRequest, result: ToolCallResult) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                ToolAuditRecord(
                    call_id=result.call_id,
                    task_id=result.task_id,
                    tool_name=result.tool_name,
                    status=result.status,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    duration_ms=result.duration_ms,
                    arguments=request.arguments,
                    output=result.output,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            )
        except Exception:
            _LOGGER.warning("Tool audit recording failed")
