import importlib
import os
import subprocess
import sys
from pathlib import Path


def test_m08_public_symbols_are_exported_from_the_stable_package_path():
    from data_analysis_agent import (
        AgentCheckpoint,
        AgentOrchestrationError,
        AgentOrchestrator,
        LegacyAnalysisAdapter,
        OrchestrationResult,
        OrchestratorBudgetError,
        OrchestratorLimits,
        StageExecutionError,
        StageFailure,
        StageInput,
        StageResult,
        TaskStatus,
        ToolUnavailableError,
        DisallowedToolError,
        InvalidCheckpointError,
    )

    assert AgentOrchestrator.__name__ == "AgentOrchestrator"
    assert LegacyAnalysisAdapter.__name__ == "LegacyAnalysisAdapter"
    assert AgentCheckpoint.__name__ == "AgentCheckpoint"
    assert OrchestrationResult.__name__ == "OrchestrationResult"
    assert OrchestratorLimits.__name__ == "OrchestratorLimits"
    assert StageFailure.__name__ == "StageFailure"
    assert StageInput.__name__ == "StageInput"
    assert StageResult.__name__ == "StageResult"
    assert TaskStatus.COMPLETED.value == "COMPLETED"
    assert AgentOrchestrationError.__name__ == "AgentOrchestrationError"
    assert InvalidCheckpointError.__bases__ == (AgentOrchestrationError,)
    assert StageExecutionError.__bases__ == (AgentOrchestrationError,)
    assert DisallowedToolError.__bases__ == (AgentOrchestrationError,)
    assert ToolUnavailableError.__bases__ == (AgentOrchestrationError,)
    assert OrchestratorBudgetError.__bases__ == (AgentOrchestrationError,)


def test_module_entry_point_is_importable_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    module = importlib.import_module("data_analysis_agent.__main__")

    assert callable(module.main)


def test_module_help_starts_without_api_key():
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment["PYTHONPATH"] = str(project_root / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "data_analysis_agent", "--help"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "usage:" in completed.stdout.lower()
