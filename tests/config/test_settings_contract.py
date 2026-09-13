from pathlib import Path
import logging

import pytest

from data_analysis_agent.config.settings import (
    ConfigurationError,
    Settings,
    configure_logging,
    load_settings,
)
from data_analysis_agent.config.llm import LLMConfig


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
    assert "DATABASE_URL" in message
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
        "DATABASE_URL": "sqlite:///production-test.sqlite3",
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
            "DATABASE_URL": "sqlite:///production-test.sqlite3",
        },
        dotenv_dir=tmp_path,
    )

    with caplog.at_level(logging.DEBUG, logger="data_analysis_agent"):
        logger = configure_logging(settings)
        llm_config = settings.llm_config()

    assert logger.name == "data_analysis_agent"
    assert isinstance(llm_config, LLMConfig)
    assert llm_config.api_key == "secret-that-must-not-be-logged"
    assert llm_config.model == "offline-model"
    assert "secret-that-must-not-be-logged" not in caplog.text
