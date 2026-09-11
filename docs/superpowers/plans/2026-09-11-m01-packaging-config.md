# M01 项目打包与配置管理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将项目整理为可通过 pip install -e . 安装的 data-analysis-agent 应用，使用唯一的 src/data_analysis_agent 生产包，并保留可工作的根目录兼容入口与隔离的类型化配置。

**Architecture:** 生产实现迁移到 src/data_analysis_agent/，按 config、agent、execution、reports、services 分层。根目录同名模块通过 package-shaped bridge 加载 src 包，旧的 config、utils、prompts.py 和 main.py 只做转发。配置使用不修改进程环境的 dotenv 读取器，Settings 负责环境、资源、日志和 LLM 配置，CLI 与 quick_analysis 复用同一公共实现。

**Tech Stack:** Python 3.10+, setuptools, dataclasses, python-dotenv, pytest, IPython, Matplotlib, pandas, OpenAI-compatible client, python-docx.

## Global Constraints

- Distribution name is data-analysis-agent; import package is data_analysis_agent.
- Python requirement is >=3.10.
- Production source of truth is src/data_analysis_agent/; root compatibility modules contain no duplicate business implementation.
- New package modules use package-relative imports; only config/settings.py may read os.environ as loader input.
- test configuration never reads ordinary .env or production dotenv files and never requires an API key.
- production configuration reports missing OPENAI_API_KEY, OPENAI_BASE_URL, or OPENAI_MODEL through ConfigurationError with field names and without secret values.
- API keys are absent from source code, example files, logs, and exception text.
- quick_analysis uses the exact signature approved in the design document.
- Every production change starts with a failing test and keeps the M00 31-test behavior suite green after the task.
- Do not reset, checkout, delete, or overwrite the existing uncommitted M00 tests and fixtures.
- Do not add database, Redis, storage clients, timeout enforcement, upload handling, or new Agent behavior in M01.

---

## File map

New production and packaging files:

- pyproject.toml: setuptools metadata, runtime dependencies, dev extra, pytest settings, console script.
- requirements-dev.txt: development convenience install containing -e .[dev].
- .env.development.example, .env.test.example, .env.production.example: secret-free templates.
- src/data_analysis_agent/__init__.py: canonical public exports.
- src/data_analysis_agent/__main__.py: module entry point.
- src/data_analysis_agent/cli.py: one argparse-based CLI implementation.
- src/data_analysis_agent/config/__init__.py, config/llm.py, config/settings.py.
- src/data_analysis_agent/agent/__init__.py, agent/core.py, agent/prompts.py.
- src/data_analysis_agent/execution/__init__.py, execution/code_executor.py.
- src/data_analysis_agent/reports/__init__.py, reports/word.py.
- src/data_analysis_agent/services/__init__.py, services/llm.py, services/openai_client.py, services/responses.py, services/session.py.

Compatibility files:

- data_analysis_agent.py: package-shaped bridge that executes the canonical src package namespace.
- main.py: thin call-through to data_analysis_agent.cli.main.
- config/__init__.py and config/llm_config.py: old-path exports.
- every root utils module: old-path forwarding exports.
- prompts.py: old-path prompt exports.
- .gitignore: real environment ignores and example-file exceptions.
- requirements.txt: runtime-only dependency list.

New tests:

- tests/config/__init__.py and tests/config/test_settings_contract.py.
- tests/packaging/__init__.py and tests/packaging/test_package_contract.py.
- tests/public_api/__init__.py and tests/public_api/test_public_api_contract.py.
- Existing M00 tests: only monkeypatch/import targets may change when implementation moves; scenarios and assertions stay unchanged.

---

## Task 1: Add red tests for typed settings, packaging, and canonical API

Files:

- Create: tests/config/__init__.py
- Create: tests/config/test_settings_contract.py
- Create: tests/packaging/__init__.py
- Create: tests/packaging/test_package_contract.py
- Create: tests/public_api/__init__.py
- Create: tests/public_api/test_public_api_contract.py

Interfaces:

- Tests import data_analysis_agent.config.settings.ConfigurationError, Settings, configure_logging, and load_settings.
- Tests import data_analysis_agent.config.llm.LLMConfig.
- Top-level data_analysis_agent exports DataAnalysisAgent, LLMConfig, Settings, ConfigurationError, and quick_analysis.
- quick_analysis parameters are query, files, keyword-only output_dir, max_rounds, generate_word_report, and settings.

- [ ] Step 1: Write the failing settings tests

Create tests/config/test_settings_contract.py:

~~~python
from pathlib import Path
import logging

import pytest

from data_analysis_agent.config.settings import (
    ConfigurationError,
    Settings,
    configure_logging,
    load_settings,
)


def test_development_defaults_allow_offline_construction(tmp_path):
    settings = load_settings(
        app_env="development", environ={}, dotenv_dir=tmp_path
    )

    assert isinstance(settings, Settings)
    assert settings.app_env == "development"
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.deepseek.com"
    assert settings.openai_model == "deepseek-chat"
    assert settings.max_task_runtime == 900
    assert settings.max_upload_size == 104857600
    assert settings.output_dir == Path("outputs")
    assert settings.log_level == "INFO"


def test_test_profile_does_not_read_ordinary_or_production_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=ordinary-secret\nOPENAI_MODEL=ordinary-model\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.production").write_text(
        "OPENAI_API_KEY=production-secret\nOPENAI_MODEL=production-model\n",
        encoding="utf-8",
    )

    settings = load_settings(app_env="test", environ={}, dotenv_dir=tmp_path)

    assert settings.openai_api_key is None
    assert settings.openai_model == "deepseek-chat"


def test_process_environment_overrides_selected_dotenv_file(tmp_path):
    (tmp_path / ".env.development").write_text(
        "OPENAI_API_KEY=file-secret\nOPENAI_MODEL=file-model\nMAX_TASK_RUNTIME=120\n",
        encoding="utf-8",
    )

    settings = load_settings(
        app_env="development",
        environ={
            "OPENAI_API_KEY": "process-secret",
            "OPENAI_MODEL": "process-model",
            "MAX_TASK_RUNTIME": "300",
        },
        dotenv_dir=tmp_path,
    )

    assert settings.openai_api_key == "process-secret"
    assert settings.openai_model == "process-model"
    assert settings.max_task_runtime == 300


def test_production_missing_fields_are_named_without_values(tmp_path):
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(app_env="production", environ={}, dotenv_dir=tmp_path)

    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "OPENAI_BASE_URL" in message
    assert "OPENAI_MODEL" in message
    assert "secret-value" not in message


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_TASK_RUNTIME", "0"),
        ("MAX_TASK_RUNTIME", "not-an-int"),
        ("MAX_UPLOAD_SIZE", "-1"),
        ("LOG_LEVEL", "verbose"),
    ],
)
def test_invalid_values_name_their_configuration_key(tmp_path, key, value):
    environ = {
        "OPENAI_API_KEY": "offline-key",
        "OPENAI_BASE_URL": "https://offline.invalid",
        "OPENAI_MODEL": "offline-model",
        key: value,
    }

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(app_env="production", environ=environ, dotenv_dir=tmp_path)

    assert key in str(exc_info.value)


def test_invalid_environment_name_is_explicit(tmp_path):
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        load_settings(environ={"APP_ENV": "staging"}, dotenv_dir=tmp_path)


def test_settings_produce_typed_llm_config_without_logging_secret(tmp_path, caplog):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "secret-that-must-not-be-logged",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
        },
        dotenv_dir=tmp_path,
    )

    with caplog.at_level(logging.DEBUG, logger="data_analysis_agent"):
        logger = configure_logging(settings)
        llm_config = settings.llm_config()

    assert logger.name == "data_analysis_agent"
    assert llm_config.api_key == "secret-that-must-not-be-logged"
    assert llm_config.model == "offline-model"
    assert "secret-that-must-not-be-logged" not in caplog.text
~~~

- [ ] Step 2: Run settings tests and verify the expected red state

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/config/test_settings_contract.py
~~~

Expected result before implementation: collection fails because data_analysis_agent.config.settings does not exist. Do not add a root fallback to make this collect.

- [ ] Step 3: Write the failing packaging and public API tests

Create tests/packaging/test_package_contract.py:

~~~python
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
~~~

Create tests/public_api/test_public_api_contract.py:

~~~python
import inspect

import data_analysis_agent


def test_canonical_package_exports_public_objects():
    assert callable(data_analysis_agent.quick_analysis)
    assert data_analysis_agent.DataAnalysisAgent.__module__ == (
        "data_analysis_agent.agent.core"
    )
    assert data_analysis_agent.LLMConfig.__module__ == "data_analysis_agent.config.llm"
    assert data_analysis_agent.Settings.__module__ == "data_analysis_agent.config.settings"


def test_quick_analysis_signature_is_stable():
    parameters = inspect.signature(data_analysis_agent.quick_analysis).parameters

    assert list(parameters) == [
        "query",
        "files",
        "output_dir",
        "max_rounds",
        "generate_word_report",
        "settings",
    ]
    assert parameters["query"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["files"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("output_dir", "max_rounds", "generate_word_report", "settings")
    )
~~~

- [ ] Step 4: Run all new red tests

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/config tests/packaging tests/public_api
~~~

Expected result before implementation: canonical imports and pyproject.toml assertions fail. The existing 31 M00 tests remain untouched.

- [ ] Step 5: Commit only the new red tests

~~~powershell
git add tests/config tests/packaging tests/public_api
git commit -m "test: define M01 packaging and configuration contracts"
~~~

Do not use git add . because the M00 tests and fixtures are intentionally still uncommitted.

---

## Task 2: Implement typed settings, logging, package metadata, and environment templates

Files:

- Create: pyproject.toml
- Create: requirements-dev.txt
- Create: .env.development.example
- Create: .env.test.example
- Create: .env.production.example
- Create: src/data_analysis_agent/config/__init__.py
- Create: src/data_analysis_agent/config/llm.py
- Create: src/data_analysis_agent/config/settings.py
- Modify: .gitignore
- Modify: requirements.txt
- Test: tests/config/test_settings_contract.py

Interfaces:

- ConfigurationError is a ValueError subclass and never contains secret values.
- LLMConfig is frozen and exposes provider, api_key, base_url, model, temperature, max_tokens.
- Settings is frozen and exposes all design-document fields plus llm_config().
- load_settings(app_env=None, *, environ=None, dotenv_dir=None, output_dir=None) -> Settings never mutates os.environ.
- configure_logging(settings) -> logging.Logger configures the data_analysis_agent logger.

- [ ] Step 1: Implement LLMConfig

Create src/data_analysis_agent/config/llm.py:

~~~python
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.1
    max_tokens: int = 8192

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMConfig":
        return cls(**data)

    def validate(self) -> bool:
        missing = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.base_url:
            missing.append("OPENAI_BASE_URL")
        if not self.model:
            missing.append("OPENAI_MODEL")
        if missing:
            raise ValueError(
                "Missing required LLM configuration: " + ", ".join(missing)
            )
        return True
~~~

- [ ] Step 2: Implement non-mutating Settings loading

Create src/data_analysis_agent/config/settings.py. It must use dotenv_values, not load_dotenv:

~~~python
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional

from dotenv import dotenv_values

from .llm import LLMConfig


EnvironmentName = Literal["development", "test", "production"]
VALID_ENVIRONMENTS = {"development", "test", "production"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_TASK_RUNTIME = 900
DEFAULT_MAX_UPLOAD_SIZE = 104857600
LOGGER_NAME = "data_analysis_agent"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    app_env: EnvironmentName
    database_url: Optional[str]
    redis_url: Optional[str]
    storage_endpoint: Optional[str]
    storage_bucket: Optional[str]
    openai_api_key: Optional[str]
    openai_base_url: str
    openai_model: str
    max_task_runtime: int
    max_upload_size: int
    output_dir: Path
    log_level: str

    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider="deepseek",
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            model=self.openai_model,
        )


def _get_environment(raw: str) -> EnvironmentName:
    if raw not in VALID_ENVIRONMENTS:
        raise ConfigurationError(
            "APP_ENV must be one of development, test, or production"
        )
    return raw


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{key} must be a positive integer")
    return parsed


def _read_values(
    environment: EnvironmentName,
    environ: Mapping[str, str],
    dotenv_dir: Path,
) -> Dict[str, str]:
    values: Dict[str, str] = {}
    selected = dotenv_dir / f".env.{environment}"
    if selected.is_file():
        values.update(
            {
                key: value
                for key, value in dotenv_values(selected).items()
                if value is not None
            }
        )
    elif environment == "development":
        fallback = dotenv_dir / ".env"
        if fallback.is_file():
            values.update(
                {
                    key: value
                    for key, value in dotenv_values(fallback).items()
                    if value is not None
                }
            )
    values.update({key: value for key, value in environ.items() if value is not None})
    return values


def load_settings(
    app_env: EnvironmentName | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_dir: Path | None = None,
    output_dir: str | Path | None = None,
) -> Settings:
    provided = environ if environ is not None else os.environ
    environment = _get_environment(
        app_env or provided.get("APP_ENV", "development")
    )
    values = _read_values(
        environment, provided, Path(dotenv_dir or Path.cwd())
    )
    defaults = {
        "development": "outputs",
        "test": "outputs/test",
        "production": "outputs/production",
    }
    selected_output = output_dir or values.get("OUTPUT_DIR") or defaults[environment]
    settings = Settings(
        app_env=environment,
        database_url=values.get("DATABASE_URL"),
        redis_url=values.get("REDIS_URL"),
        storage_endpoint=values.get("STORAGE_ENDPOINT"),
        storage_bucket=values.get("STORAGE_BUCKET"),
        openai_api_key=values.get("OPENAI_API_KEY") or None,
        openai_base_url=values.get(
            "OPENAI_BASE_URL",
            DEFAULT_BASE_URL if environment != "production" else "",
        ),
        openai_model=values.get(
            "OPENAI_MODEL", DEFAULT_MODEL if environment != "production" else ""
        ),
        max_task_runtime=_positive_int(
            values, "MAX_TASK_RUNTIME", DEFAULT_MAX_TASK_RUNTIME
        ),
        max_upload_size=_positive_int(
            values, "MAX_UPLOAD_SIZE", DEFAULT_MAX_UPLOAD_SIZE
        ),
        output_dir=Path(selected_output),
        log_level=values.get("LOG_LEVEL", "INFO").upper(),
    )
    if settings.log_level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, or ERROR"
        )
    if environment == "production":
        missing = [
            field
            for field, value in (
                ("OPENAI_API_KEY", settings.openai_api_key),
                ("OPENAI_BASE_URL", settings.openai_base_url),
                ("OPENAI_MODEL", settings.openai_model),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required production configuration: " + ", ".join(missing)
            )
    return settings


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.log_level))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            )
        )
        logger.addHandler(handler)
    logger.propagate = True
    return logger
~~~

The only os.environ use is the input fallback in load_settings. The loader must not mutate process environment or log the values mapping.

- [ ] Step 3: Add config exports and templates

Create src/data_analysis_agent/config/__init__.py:

~~~python
from .llm import LLMConfig
from .settings import ConfigurationError, Settings, configure_logging, load_settings

__all__ = [
    "ConfigurationError",
    "LLMConfig",
    "Settings",
    "configure_logging",
    "load_settings",
]
~~~

Create all three example files with these keys. Keep OPENAI_API_KEY empty and change APP_ENV and OUTPUT_DIR per filename:

~~~text
APP_ENV=development
DATABASE_URL=
REDIS_URL=
STORAGE_ENDPOINT=
STORAGE_BUCKET=
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
MAX_TASK_RUNTIME=900
MAX_UPLOAD_SIZE=104857600
OUTPUT_DIR=outputs
LOG_LEVEL=INFO
~~~

Add .env.development, .env.test, and .env.production to .gitignore. Keep the example files trackable.

- [ ] Step 4: Add setuptools metadata and dependency separation

Create pyproject.toml:

~~~toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "data-analysis-agent"
version = "0.2.0"
description = "Installable data analysis agent with offline-testable LLM orchestration"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "pandas>=2.0.0",
    "numpy>=1.24.0",
    "matplotlib>=3.6.0",
    "duckdb>=0.8.0",
    "scipy>=1.10.0",
    "scikit-learn>=1.3.0",
    "plotly>=5.14.0",
    "requests>=2.28.0",
    "urllib3>=1.26.0",
    "ipython>=8.10.0",
    "openai>=1.0.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "python-docx>=0.8.11",
    "typing-extensions>=4.5.0",
    "fonttools>=4.38.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0.0",
    "flake8>=6.0.0",
]

[project.scripts]
data-analysis-agent = "data_analysis_agent.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra"
~~~

Replace requirements.txt with the exact project dependencies and create requirements-dev.txt containing -e .[dev]. Remove pytest, pytest-asyncio, black, and flake8 from runtime requirements.

- [ ] Step 5: Run focused settings and metadata tests

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/config tests/packaging/test_package_contract.py::test_project_metadata_declares_src_layout_and_console_script
~~~

Expected result: all settings tests and metadata assertions pass. Module help can remain red until Task 5 creates the module entry point.

- [ ] Step 6: Commit Task 2 without staging unrelated M00 work

~~~powershell
git add pyproject.toml requirements.txt requirements-dev.txt .gitignore .env.development.example .env.test.example .env.production.example src/data_analysis_agent/config tests/config tests/packaging/test_package_contract.py
git commit -m "feat: add typed environment configuration and package metadata"
~~~

---

## Task 3: Migrate the runtime into the canonical src package

Files:

- Create: src/data_analysis_agent/__init__.py
- Create: src/data_analysis_agent/agent/__init__.py
- Create: src/data_analysis_agent/agent/core.py
- Create: src/data_analysis_agent/agent/prompts.py
- Create: src/data_analysis_agent/execution/__init__.py
- Create: src/data_analysis_agent/execution/code_executor.py
- Create: src/data_analysis_agent/reports/__init__.py
- Create: src/data_analysis_agent/reports/word.py
- Create: src/data_analysis_agent/services/__init__.py
- Create: src/data_analysis_agent/services/llm.py
- Create: src/data_analysis_agent/services/openai_client.py
- Create: src/data_analysis_agent/services/responses.py
- Create: src/data_analysis_agent/services/session.py
- Test: tests/public_api/test_public_api_contract.py
- Test: all M00 contract and integration tests

Interfaces:

- DataAnalysisAgent preserves the M00 constructor, analyze, _process_response, _generate_final_report, reset, and return dictionary.
- quick_analysis is implemented only in agent/core.py.
- CodeExecutor, LLMHelper, generate_word_report, create_session_output_dir, extract_code_from_response, and format_execution_result preserve current signatures and output fields.
- Canonical modules import neighbors relatively.

- [ ] Step 1: Create package directories and explicit exports

Create the package __init__ files. Top-level src/data_analysis_agent/__init__.py must contain:

~~~python
from .agent.core import DataAnalysisAgent, quick_analysis
from .config import (
    ConfigurationError,
    LLMConfig,
    Settings,
    configure_logging,
    load_settings,
)
from .execution.code_executor import CodeExecutor

__all__ = [
    "CodeExecutor",
    "ConfigurationError",
    "DataAnalysisAgent",
    "LLMConfig",
    "Settings",
    "configure_logging",
    "load_settings",
    "quick_analysis",
]
~~~

- [ ] Step 2: Copy the current runtime implementations to canonical locations

Use these mappings. Change only imports, module docstrings, and diagnostic logger calls; preserve behavior:

~~~text
utils/fallback_openai_client.py -> src/data_analysis_agent/services/openai_client.py
utils/llm_helper.py -> src/data_analysis_agent/services/llm.py
utils/extract_code.py + utils/format_execution_result.py -> src/data_analysis_agent/services/responses.py
utils/create_session_dir.py -> src/data_analysis_agent/services/session.py
utils/code_executor.py -> src/data_analysis_agent/execution/code_executor.py
utils/word_report_generator.py -> src/data_analysis_agent/reports/word.py
prompts.py -> src/data_analysis_agent/agent/prompts.py
~~~

Use these imports in the moved Agent and LLM modules:

~~~python
from ..config.llm import LLMConfig
from ..config.settings import Settings, load_settings
from ..execution.code_executor import CodeExecutor
from ..reports.word import generate_word_report
from ..services.llm import LLMHelper
from ..services.responses import extract_code_from_response, format_execution_result
from ..services.session import create_session_output_dir
from .prompts import data_analysis_system_prompt, final_report_system_prompt
~~~

Preserve CodeExecutor result keys success, output, error, variables. Preserve Agent result keys session_output_dir, total_rounds, analysis_results, collected_figures, conversation_history, final_report, report_file_path, word_report_file_path, word_report_generated, and word_report_error. Keep existing progress output so M00 behavior does not change.

- [ ] Step 3: Replace only quick_analysis with the approved adapter

In agent/core.py use:

~~~python
def quick_analysis(
    query: str,
    files: Sequence[str] | None = None,
    *,
    output_dir: str | Path | None = None,
    max_rounds: int | None = None,
    generate_word_report: bool | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or load_settings()
    agent = DataAnalysisAgent(
        llm_config=resolved_settings.llm_config(),
        output_dir=str(output_dir or resolved_settings.output_dir),
        max_rounds=max_rounds if max_rounds is not None else 10,
        generate_word_report=(
            generate_word_report
            if generate_word_report is not None
            else resolved_settings.app_env != "test"
        ),
    )
    return agent.analyze(
        user_input=query,
        files=list(files) if files is not None else None,
    )
~~~

Add imports from pathlib import Path and from typing import Any, Sequence. Explicit function arguments override Settings.

- [ ] Step 4: Complete canonical subpackage exports

Use:

~~~python
# agent/__init__.py
from .core import DataAnalysisAgent, quick_analysis
__all__ = ["DataAnalysisAgent", "quick_analysis"]

# execution/__init__.py
from .code_executor import CodeExecutor
__all__ = ["CodeExecutor"]

# reports/__init__.py
from .word import WordReportGenerator, generate_word_report
__all__ = ["WordReportGenerator", "generate_word_report"]

# services/__init__.py
from .llm import LLMHelper
from .openai_client import AsyncFallbackOpenAIClient
from .responses import extract_code_from_response, format_execution_result
from .session import create_session_output_dir
__all__ = [
    "AsyncFallbackOpenAIClient",
    "LLMHelper",
    "create_session_output_dir",
    "extract_code_from_response",
    "format_execution_result",
]
~~~

- [ ] Step 5: Run canonical import and M00 migration tests

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/public_api tests/contract tests/integration tests/test_llm_helper.py tests/test_prompt_contract.py tests/test_word_report_generator.py tests/test_word_report_integration.py tests/test_public_api.py
~~~

Expected result: canonical imports work and all M00 scenarios pass. If a test patches a moved module global, update only its target to data_analysis_agent.agent.core. Do not add duplicate production exports.

- [ ] Step 6: Commit the canonical source migration

~~~powershell
git add src/data_analysis_agent tests/public_api tests/contract tests/integration tests/test_llm_helper.py tests/test_prompt_contract.py tests/test_word_report_generator.py tests/test_word_report_integration.py tests/test_public_api.py
git commit -m "refactor: migrate runtime into canonical source package"
~~~

---

## Task 4: Replace root implementations with compatibility bridges

Files:

- Modify: data_analysis_agent.py
- Modify: main.py
- Modify: config/__init__.py and config/llm_config.py
- Modify: every root utils module and prompts.py
- Modify: M00 monkeypatch targets only where moved globals require it
- Test: tests/packaging/test_package_contract.py
- Test: tests/public_api/test_public_api_contract.py

Interfaces:

- Root imports expose identity-equal canonical objects.
- Existing M00 imports remain valid.
- Root data_analysis_agent.py supports ordinary import and python -m data_analysis_agent from a checkout.

- [ ] Step 1: Add failing legacy identity tests

Append to tests/packaging/test_package_contract.py:

~~~python
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
~~~

- [ ] Step 2: Implement the package-shaped root bridge

Replace root data_analysis_agent.py with:

~~~python
"""Compatibility bridge for running the source package from a checkout."""

from importlib.util import spec_from_file_location
from pathlib import Path
import sys


_SOURCE_ROOT = Path(__file__).with_name("src")
_SOURCE_PACKAGE = _SOURCE_ROOT / "data_analysis_agent"
_INIT_FILE = _SOURCE_PACKAGE / "__init__.py"
if not _INIT_FILE.is_file():
    raise ImportError(f"Canonical package not found: {_SOURCE_PACKAGE}")

if __name__ == "__main__":
    sys.path.insert(0, str(_SOURCE_ROOT))
    from data_analysis_agent.cli import main
    raise SystemExit(main())

__file__ = str(_INIT_FILE)
__path__ = [str(_SOURCE_PACKAGE)]
__package__ = __name__
__spec__ = spec_from_file_location(
    __name__, str(_INIT_FILE), submodule_search_locations=[str(_SOURCE_PACKAGE)]
)
exec(
    compile(_INIT_FILE.read_text(encoding="utf-8"), str(_INIT_FILE), "exec"),
    globals(),
    globals(),
)
~~~

The bridge must not define another Agent class or quick_analysis function.

- [ ] Step 3: Replace legacy modules with forwarding imports

Use these bodies:

~~~python
# config/llm_config.py
from data_analysis_agent.config.llm import LLMConfig
__all__ = ["LLMConfig"]

# config/__init__.py
from data_analysis_agent.config import (
    ConfigurationError,
    LLMConfig,
    Settings,
    configure_logging,
    load_settings,
)
__all__ = [
    "ConfigurationError",
    "LLMConfig",
    "Settings",
    "configure_logging",
    "load_settings",
]

# utils/code_executor.py
from data_analysis_agent.execution.code_executor import CodeExecutor
__all__ = ["CodeExecutor"]

# utils/create_session_dir.py
from data_analysis_agent.services.session import create_session_output_dir
__all__ = ["create_session_output_dir"]

# utils/extract_code.py
from data_analysis_agent.services.responses import extract_code_from_response
__all__ = ["extract_code_from_response"]

# utils/fallback_openai_client.py
from data_analysis_agent.services.openai_client import AsyncFallbackOpenAIClient
__all__ = ["AsyncFallbackOpenAIClient"]

# utils/format_execution_result.py
from data_analysis_agent.services.responses import format_execution_result
__all__ = ["format_execution_result"]

# utils/llm_helper.py
from data_analysis_agent.services.llm import LLMHelper
__all__ = ["LLMHelper"]

# utils/word_report_generator.py
from data_analysis_agent.reports.word import WordReportGenerator, generate_word_report
__all__ = ["WordReportGenerator", "generate_word_report"]

# utils/__init__.py
from data_analysis_agent.execution.code_executor import CodeExecutor
from data_analysis_agent.services.llm import LLMHelper
from data_analysis_agent.services.openai_client import AsyncFallbackOpenAIClient
__all__ = ["AsyncFallbackOpenAIClient", "CodeExecutor", "LLMHelper"]

# prompts.py
from data_analysis_agent.agent.prompts import (
    data_analysis_system_prompt,
    final_report_system_prompt,
)
__all__ = ["data_analysis_system_prompt", "final_report_system_prompt"]
~~~

- [ ] Step 4: Make root main.py a CLI bridge

~~~python
from data_analysis_agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
~~~

- [ ] Step 5: Update only M00 monkeypatch targets

Keep scenario assertions unchanged. Replace patch targets that refer to old root globals:

~~~python
monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", RecordingExecutor)
monkeypatch.setattr("data_analysis_agent.agent.core.generate_word_report", fail_conversion)
~~~

- [ ] Step 6: Run compatibility and behavior tests

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/packaging tests/public_api tests/contract tests/integration
~~~

Expected result: legacy objects are identity-equal to canonical objects and M00 behavior remains green.

- [ ] Step 7: Commit compatibility bridges

~~~powershell
git add data_analysis_agent.py main.py config utils prompts.py tests/packaging tests/public_api tests/contract tests/integration
git commit -m "refactor: bridge legacy imports to source package"
~~~

---

## Task 5: Add the single CLI and module entry points

Files:

- Create: src/data_analysis_agent/cli.py
- Create: src/data_analysis_agent/__main__.py
- Modify: main.py
- Test: tests/packaging/test_package_contract.py

Interfaces:

- build_parser() returns one argparse parser.
- main(argv: Sequence[str] | None = None) -> int returns a process exit code.
- python -m data_analysis_agent, data-analysis-agent, and python main.py use the same CLI.

- [ ] Step 1: Add failing CLI tests

Append:

~~~python
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
~~~

- [ ] Step 2: Implement cli.py

~~~python
import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .agent.core import quick_analysis
from .config.settings import ConfigurationError, configure_logging, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the data analysis agent against input files"
    )
    parser.add_argument("files", nargs="*", help="CSV or other input data files")
    parser.add_argument("--query", default="分析输入数据并生成关键发现和图表")
    parser.add_argument(
        "--env", choices=("development", "test", "production"), default=None
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--no-word-report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(
            app_env=args.env,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        configure_logging(settings)
        missing = [file for file in args.files if not Path(file).is_file()]
        if missing:
            print(
                "Input files do not exist: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        result = quick_analysis(
            query=args.query,
            files=args.files,
            output_dir=args.output_dir,
            max_rounds=args.max_rounds,
            generate_word_report=not args.no_word_report,
            settings=settings,
        )
        print(result)
        return 0
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        logging.getLogger("data_analysis_agent").exception("Analysis failed")
        return 1
~~~

The CLI prints only final results and explicit file/configuration errors; it never prints Settings values or API keys.

- [ ] Step 3: Implement __main__.py and keep root script bridge

src/data_analysis_agent/__main__.py:

~~~python
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
~~~

Root main.py remains the exact bridge from Task 4.

- [ ] Step 4: Run CLI tests and direct help commands

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/packaging tests/public_api
.\.venv\Scripts\python.exe -m data_analysis_agent --help
.\.venv\Scripts\python.exe main.py --help
~~~

Expected result: both help commands exit 0 and print identical options; production without required configuration exits nonzero and names all missing fields.

- [ ] Step 5: Commit CLI entry points

~~~powershell
git add src/data_analysis_agent/cli.py src/data_analysis_agent/__main__.py main.py tests/packaging tests/public_api
git commit -m "feat: add module and console CLI entry points"
~~~

---

## Task 6: Verify editable installation, external imports, dependency separation, and full regression

Files:

- Modify: tests/packaging/test_package_contract.py for external-process verification.
- Verify: pyproject.toml, requirements.txt, requirements-dev.txt, environment templates, src package, and root bridges.

Interfaces:

- pip install -e . installs distribution data-analysis-agent and exposes data-analysis-agent on PATH.
- An interpreter started outside the repository imports the installed source package, not root data_analysis_agent.py.
- The full suite passes with empty API environment and no network request.

- [ ] Step 1: Add the external import test and observe pre-install red state

Append:

~~~python
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
~~~

Run before installation:

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/packaging/test_package_contract.py -k external
~~~

Expected result before editable installation: the external process cannot import the package. Do not weaken the assertion.

- [ ] Step 2: Install editable package

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e .
~~~

Expected result: pip builds the editable data-analysis-agent installation without an API key or an LLM call.

- [ ] Step 3: Verify external import and console script

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/packaging/test_package_contract.py -k external
.\.venv\Scripts\data-analysis-agent.exe --help
~~~

Expected result: external import succeeds, quick_analysis.__module__ is data_analysis_agent.agent.core, and console help exits 0.

- [ ] Step 4: Run the complete no-key regression suite

~~~powershell
$env:OPENAI_API_KEY = ""
$env:OPENAI_BASE_URL = ""
$env:OPENAI_MODEL = ""
.\.venv\Scripts\python.exe -m pytest -q
~~~

Expected result: all M00 and M01 tests pass; no test calls a real model and no API key is required.

- [ ] Step 5: Run compile, dependency, and whitespace checks

~~~powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
~~~

Expected result: all commands exit 0, no broken dependencies, and no whitespace errors.

- [ ] Step 6: Inspect import scope and secrets

~~~powershell
git status --short --untracked-files=all
rg -n "load_dotenv\(" src/data_analysis_agent
rg -n "os\.environ" src/data_analysis_agent
rg -n "OPENAI_API_KEY\s*=\s*[^#\r\n]+" src .env.*.example pyproject.toml
~~~

Interpretation:

- No load_dotenv( call may remain; only settings.py may use os.environ as loader input.
- The secret search may show empty example assignments, but no non-empty secret value.
- Git status must not show .env, generated reports, or virtual environments.

- [ ] Step 7: Record evidence and commit only M01 implementation files

Update task_plan.md, findings.md, and progress.md with exact test count, editable-install output, external import path, and limitations. Inspect the staged scope:

~~~powershell
git add pyproject.toml requirements.txt requirements-dev.txt .gitignore .env.development.example .env.test.example .env.production.example src tests/config tests/packaging tests/public_api data_analysis_agent.py main.py config utils prompts.py task_plan.md findings.md progress.md
git diff --cached --name-only
git commit -m "feat: package application and isolate environment configuration"
~~~

Remove unrelated files before committing. Never stage .env, generated outputs, virtual environments, or M00 files unless explicitly chosen.

---

## Final M01 acceptance checklist

- [ ] pip install -e . exits 0.
- [ ] python -m data_analysis_agent --help exits 0 without an API key.
- [ ] data-analysis-agent --help exits 0 and uses the same CLI.
- [ ] External import resolves to the installed src/data_analysis_agent package.
- [ ] Canonical and legacy config/util imports are identity-compatible.
- [ ] Settings is typed, frozen, and loads only the selected environment file plus explicit overrides.
- [ ] Test environment ignores ordinary .env and production dotenv files.
- [ ] Production missing configuration names fields without exposing values.
- [ ] quick_analysis has one signature and forwards query, files, output directory, round limit, Word flag, and settings.
- [ ] Production and development dependencies are separated.
- [ ] Empty API environment full regression passes, including all 31 M00 tests.
- [ ] compileall, pip check, and git diff --check pass.
