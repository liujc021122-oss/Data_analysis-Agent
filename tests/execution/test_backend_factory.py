from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.execution import (
    ContainerCodeExecutor,
    ExecutionErrorCode,
    ExecutionRequest,
    LocalCodeExecutor,
    build_execution_backend,
)


def _production_environment(**overrides: str) -> dict[str, str]:
    values = {
        "OPENAI_API_KEY": "offline-key",
        "OPENAI_BASE_URL": "https://offline.invalid",
        "OPENAI_MODEL": "offline-model",
        "DATABASE_URL": "mysql+pymysql://user:password@db.invalid/data_analysis",
        "STORAGE_ENDPOINT": "https://storage.invalid",
        "STORAGE_BUCKET": "data-analysis",
        "STORAGE_SIGNING_SECRET": "signing-secret",
        "EXECUTION_IMAGE": "analysis:offline",
    }
    values.update(overrides)
    return values


def test_development_defaults_to_local_backend(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={},
        dotenv_dir=tmp_path,
    )

    backend = build_execution_backend(settings)

    assert isinstance(backend, LocalCodeExecutor)
    assert backend.production_safe is False
    assert settings.execution_backend == "local"


def test_test_profile_does_not_inherit_production_execution_selection(tmp_path):
    (tmp_path / ".env.production").write_text(
        "EXECUTION_BACKEND=container\nEXECUTION_IMAGE=production:secret\n",
        encoding="utf-8",
    )

    settings = load_settings(app_env="test", environ={}, dotenv_dir=tmp_path)

    assert settings.execution_backend == "local"
    assert settings.execution_image is None


def test_production_requires_container_image(tmp_path):
    environ = _production_environment()
    environ.pop("EXECUTION_IMAGE")

    settings = load_settings(
        app_env="production",
        environ=environ,
        dotenv_dir=tmp_path,
    )

    with pytest.raises(ConfigurationError, match="EXECUTION_IMAGE"):
        build_execution_backend(settings)


def test_production_rejects_local_execution_backend(tmp_path):
    with pytest.raises(ConfigurationError, match="EXECUTION_BACKEND"):
        load_settings(
            app_env="production",
            environ=_production_environment(EXECUTION_BACKEND="local"),
            dotenv_dir=tmp_path,
        )


def test_unknown_execution_backend_is_explicit(tmp_path):
    with pytest.raises(ConfigurationError, match="EXECUTION_BACKEND"):
        load_settings(
            app_env="development",
            environ={"EXECUTION_BACKEND": "shell"},
            dotenv_dir=tmp_path,
        )


def test_execution_network_policy_accepts_disabled_alias_and_rejects_unknown(
    tmp_path,
):
    settings = load_settings(
        app_env="test",
        environ={"EXECUTION_NETWORK_POLICY": "disabled"},
        dotenv_dir=tmp_path,
    )
    assert settings.execution_network_mode == "none"

    with pytest.raises(ConfigurationError, match="EXECUTION_NETWORK_MODE"):
        load_settings(
            app_env="test",
            environ={"EXECUTION_NETWORK_MODE": "host"},
            dotenv_dir=tmp_path,
        )


def test_production_factory_returns_container_backend(tmp_path):
    settings = load_settings(
        app_env="production",
        environ=_production_environment(),
        dotenv_dir=tmp_path,
    )

    backend = build_execution_backend(settings)

    assert isinstance(backend, ContainerCodeExecutor)
    assert backend.production_safe is True
    assert backend.image == "analysis:offline"


class _UnavailableRuntime:
    def available(self) -> bool:
        return False


def test_production_runtime_unavailable_does_not_fallback_to_local(
    tmp_path: Path,
):
    settings = load_settings(
        app_env="production",
        environ=_production_environment(),
        dotenv_dir=tmp_path,
    )
    backend = build_execution_backend(settings, runtime=_UnavailableRuntime())
    output_dir = tmp_path / "outputs" / "task"
    result = backend.execute(
        ExecutionRequest(
            task_id=uuid4(),
            code="print('offline')",
            output_dir=output_dir,
            output_scope=tmp_path / "outputs",
        )
    )

    assert isinstance(backend, ContainerCodeExecutor)
    assert not isinstance(backend, LocalCodeExecutor)
    assert result.success is False
    assert result.error_code is ExecutionErrorCode.BACKEND_UNAVAILABLE
