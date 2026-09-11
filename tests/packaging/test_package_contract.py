from pathlib import Path
import inspect
import os
import subprocess
import sys

import data_analysis_agent


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
