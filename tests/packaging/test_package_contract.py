from pathlib import Path
import os
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_project_metadata_declares_src_layout_and_console_script():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "data-analysis-agent"' in pyproject
    assert 'requires-python = ">=3.10"' in pyproject
    assert 'package-dir = {"" = "src"}' in pyproject
    assert 'data-analysis-agent = "data_analysis_agent.cli:main"' in pyproject


def test_module_help_does_not_require_api_key():
    environment = os.environ.copy()
    environment.update(
        {
            "OPENAI_API_KEY": "",
            "OPENAI_BASE_URL": "",
            "OPENAI_MODEL": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "data_analysis_agent", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "OPENAI_API_KEY" not in result.stderr


def test_legacy_config_exports_canonical_objects():
    from config.llm_config import LLMConfig as LegacyLLMConfig
    from data_analysis_agent import LLMConfig, Settings

    assert LegacyLLMConfig is LLMConfig
    assert Settings.__module__ == "data_analysis_agent.config.settings"


def test_legacy_utils_export_canonical_objects():
    from data_analysis_agent.execution.code_executor import CodeExecutor
    from utils.code_executor import CodeExecutor as LegacyCodeExecutor
    from data_analysis_agent.services.llm import LLMHelper
    from utils.llm_helper import LLMHelper as LegacyLLMHelper

    assert LegacyCodeExecutor is CodeExecutor
    assert LegacyLLMHelper is LLMHelper


def test_module_help_and_root_script_help_match():
    module_result = subprocess.run(
        [sys.executable, "-m", "data_analysis_agent", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    script_result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert module_result.returncode == 0
    assert script_result.returncode == 0
    assert "--output-dir" in module_result.stdout
    assert module_result.stdout == script_result.stdout


def test_cli_reports_missing_production_configuration():
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "OPENAI_API_KEY": "",
            "OPENAI_BASE_URL": "",
            "OPENAI_MODEL": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "data_analysis_agent", "--env", "production"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "OPENAI_API_KEY" in result.stderr
    assert "OPENAI_BASE_URL" in result.stderr
    assert "OPENAI_MODEL" in result.stderr


def test_external_process_imports_installed_package(tmp_path):
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import data_analysis_agent; "
                "from data_analysis_agent import quick_analysis; "
                "print(data_analysis_agent.__file__); "
                "print(quick_analysis.__module__)"
            ),
        ],
        cwd=external_dir,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert result.returncode == 0
    assert "data_analysis_agent.agent.core" in result.stdout
    assert "data_analysis_agent.py" not in result.stdout
