from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Iterable, Mapping
from uuid import UUID

from .backend import CodeExecutionBackend
from .errors import CleanupFailureError, ExecutionErrorCode, sanitize_execution_text
from .models import (
    ExecutionInput,
    ExecutionAudit,
    ExecutionLimits,
    ExecutionRequest,
    ExecutionResult,
    NetworkPolicy,
    code_sha256,
)


class AgentExecutionSession:
    """Compatibility facade that runs Agent code through a typed backend.

    The historical Agent expects a stateful ``execute_code`` method and a few
    injected variables. This facade keeps that shape while creating a fresh
    typed execution request for each call. Container requests expose only
    fixed ``/input`` and ``/output`` paths; host paths remain in the request's
    private mount metadata and never enter the code protocol.
    """

    _PRELUDE = """import os
import json
import math
import re
import pathlib
import datetime
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
session_output_dir = '/output'
"""

    def __init__(
        self,
        *,
        backend: CodeExecutionBackend,
        output_dir: str | Path,
        output_scope: str | Path,
        task_id: UUID,
        limits: ExecutionLimits | None = None,
        network_policy: NetworkPolicy = NetworkPolicy.DISABLED,
    ) -> None:
        self.backend = backend
        self.output_dir = Path(output_dir).resolve(strict=False)
        self.output_scope = Path(output_scope).resolve(strict=False)
        self.task_id = task_id
        self.limits = limits or ExecutionLimits()
        self.network_policy = network_policy
        self._input_root = Path(
            tempfile.mkdtemp(prefix="analysis-input-session-")
        ).resolve()
        self._variables: dict[str, Any] = {}
        self._dataset_ids: tuple[str, ...] = ()
        self._dataset_loader: Callable[[str], Any] | None = None
        self._input_files: tuple[ExecutionInput, ...] = ()
        self._code_history: list[str] = []
        self._sensitive_columns: set[str] = set()
        self._sensitive_values: set[str] = set()
        self.audit_records: list[ExecutionAudit] = []
        self._closed = False

    @property
    def backend_name(self) -> str:
        configured_name = getattr(self.backend, "backend_name", None)
        return str(configured_name or type(self.backend).__name__)

    def set_variable(self, name: str, value: Any) -> None:
        self._variables[name] = value
        if name == "dataset_ids":
            self._dataset_ids = tuple(str(item) for item in value or ())
        elif name == "load_dataset" and callable(value):
            self._dataset_loader = value

    def set_sensitive_columns(self, names: Iterable[str]) -> None:
        self._sensitive_columns = {
            str(name).strip() for name in names if str(name).strip()
        }

    def get_environment_info(self) -> str:
        datasets = ", ".join(self._dataset_ids) if self._dataset_ids else "none"
        return (
            "Container execution environment; pandas, numpy, matplotlib, os, "
            f"json, pathlib, and load_dataset are available; "
            "session_output_dir='/output'; "
            f"dataset_ids=({datasets})"
        )

    def resolve_output_path(self, value: object) -> Path:
        """Map the fixed container output path back to the private host path."""
        text = str(value)
        if text == "/output":
            candidate = self.output_dir
        elif text.startswith("/output/"):
            candidate = self.output_dir.joinpath(text.removeprefix("/output/"))
        else:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = self.output_dir / candidate
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(self.output_dir)
        return resolved

    def _stage_dataset_inputs(self) -> None:
        if self._dataset_loader is None or not self._dataset_ids:
            self._input_files = ()
            return

        inputs: list[ExecutionInput] = []
        self._sensitive_values.clear()
        for dataset_id in self._dataset_ids:
            dataframe = self._dataset_loader(dataset_id)
            logical_name = f"datasets/{dataset_id}.csv"
            source_path = self._input_root.joinpath(*logical_name.split("/"))
            source_path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(dataframe, "to_csv"):
                raise TypeError("dataset loader must return a dataframe")
            dataframe.to_csv(source_path, index=False)
            for column in self._sensitive_columns:
                if column not in dataframe.columns:
                    continue
                for value in dataframe[column].dropna().tolist():
                    text = str(value)
                    if text:
                        self._sensitive_values.add(text)
            inputs.append(
                ExecutionInput(
                    logical_name=logical_name,
                    source_path=source_path,
                    source_scope=self._input_root,
                )
            )
        self._input_files = tuple(inputs)

    def _safe_json_variables(self) -> str:
        values: dict[str, Any] = {}
        for name, value in self._variables.items():
            if name in {"session_output_dir", "load_dataset", "dataset_ids"}:
                continue
            if not name.isidentifier():
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                values[name] = value
            elif isinstance(value, (list, tuple, dict)):
                try:
                    json.dumps(value, allow_nan=False)
                except (TypeError, ValueError):
                    continue
                values[name] = value
        if not values:
            return ""
        return "\n".join(
            f"{name} = {value!r}"
            for name, value in values.items()
        )

    def _container_prelude(self) -> str:
        dataset_paths = {
            dataset_id: f"/input/datasets/{dataset_id}.csv"
            for dataset_id in self._dataset_ids
        }
        loader = """_DATASET_PATHS = %s
dataset_ids = %s
def load_dataset(dataset_id):
    return pd.read_csv(_DATASET_PATHS[str(dataset_id)])
""" % (
            repr(dataset_paths),
            repr(self._dataset_ids),
        )
        safe_variables = self._safe_json_variables()
        return "\n".join(
            part for part in (self._PRELUDE, loader, safe_variables) if part
        )

    def _request_code(self, code: str) -> str:
        history = "\n\n".join(self._code_history)
        parts = [self._container_prelude()]
        if history:
            parts.append(history)
        parts.append(code)
        return "\n\n".join(parts)

    def _record_audit(
        self,
        result: ExecutionResult,
        *,
        started_at: datetime,
        finished_at: datetime,
    ) -> ExecutionAudit:
        audit = ExecutionAudit(
            task_id=self.task_id,
            backend=self.backend_name,
            code_sha256=result.code_sha256,
            started_at=started_at,
            finished_at=finished_at,
            success=result.success,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            resource_limited=result.resource_limited,
            error_code=result.error_code,
            duration_ms=result.duration_ms,
            output_files=result.output_files,
        )
        self.audit_records.append(audit)
        return audit

    def execute_code(self, code: str) -> dict[str, Any]:
        if self._closed:
            return {
                "success": False,
                "output": "",
                "error": "execution session is closed",
                "variables": {},
                "error_code": ExecutionErrorCode.EXECUTION_FAILED.value,
            }
        started_at = datetime.now(timezone.utc)
        request: ExecutionRequest | None = None
        try:
            self._stage_dataset_inputs()
            request = ExecutionRequest(
                task_id=self.task_id,
                code=self._request_code(code),
                input_files=self._input_files,
                output_dir=self.output_dir,
                output_scope=self.output_scope,
                limits=self.limits,
                network_policy=self.network_policy,
            )
            result = self.backend.execute(request)
            if not isinstance(result, ExecutionResult):
                raise TypeError("execution backend returned an invalid result")
            if result.success:
                self._code_history.append(code)
            finished_at = datetime.now(timezone.utc)
            audit = self._record_audit(
                result,
                started_at=started_at,
                finished_at=finished_at,
            )
            output = sanitize_execution_text(
                result.stdout,
                secrets=self._sensitive_values,
            )
            error = result.error_message or result.stderr or None
            if error is not None:
                error = sanitize_execution_text(
                    error,
                    secrets=self._sensitive_values,
                )
            return {
                "success": result.success,
                "output": output,
                "error": error or "",
                "variables": {},
                "duration_ms": int(result.duration_ms),
                "error_code": result.error_code.value if result.error_code else None,
                "files": [item.model_dump(mode="json") for item in result.output_files],
                "audit": audit.model_dump(mode="json"),
            }
        except Exception:
            finished_at = datetime.now(timezone.utc)
            fallback_result = ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                error_code=ExecutionErrorCode.EXECUTION_FAILED,
                error_message="typed execution request failed",
                code_sha256=(
                    request.code_sha256
                    if request is not None
                    else code_sha256(str(code))
                ),
                duration_ms=max(
                    0.0,
                    (finished_at - started_at).total_seconds() * 1000,
                ),
            )
            audit = self._record_audit(
                fallback_result,
                started_at=started_at,
                finished_at=finished_at,
            )
            return {
                "success": False,
                "output": "",
                "error": sanitize_execution_text(
                    "typed execution request failed",
                    secrets=self._sensitive_values,
                ),
                "variables": {},
                "error_code": ExecutionErrorCode.EXECUTION_FAILED.value,
                "audit": audit.model_dump(mode="json"),
            }

    def reset_environment(self) -> None:
        self._variables.clear()
        self._dataset_ids = ()
        self._dataset_loader = None
        self._input_files = ()
        self._code_history.clear()
        self._sensitive_values.clear()
        self.audit_records.clear()

    def close(self) -> None:
        if self._closed:
            return
        try:
            shutil.rmtree(self._input_root)
        except OSError as exc:
            raise CleanupFailureError(task_id=self.task_id) from exc
        self._closed = True


__all__ = ["AgentExecutionSession"]
