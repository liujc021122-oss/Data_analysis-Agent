from pathlib import Path
import logging
import os

import pytest

from data_analysis_agent.config.settings import (
    ConfigurationError,
    Settings,
    configure_logging,
    load_settings,
)
from data_analysis_agent.config.llm import LLMConfig
from data_analysis_agent.storage.factory import build_storage


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
    assert settings.storage_region is None
    assert settings.storage_access_key_id is None
    assert settings.storage_secret_access_key is None
    assert settings.storage_signing_secret is None
    assert settings.storage_url_expiry == 300
    assert settings.storage_retention_days == 30
    assert settings.storage_backend == "local"


def test_storage_backend_defaults_to_local_for_test(tmp_path):
    settings = load_settings(app_env="test", environ={}, dotenv_dir=tmp_path)

    assert settings.storage_backend == "local"


def test_storage_backend_selects_s3_in_development(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "STORAGE_BACKEND": "s3",
            "STORAGE_ENDPOINT": "http://minio:9000",
            "STORAGE_BUCKET": "data-analysis",
            "STORAGE_ACCESS_KEY_ID": "minioadmin",
            "STORAGE_SECRET_ACCESS_KEY": "minioadmin123",
        },
        dotenv_dir=tmp_path,
    )
    storage = build_storage(settings)

    assert storage.__class__.__name__ == "S3Storage"


def test_invalid_storage_backend_is_rejected(tmp_path):
    with pytest.raises(ConfigurationError, match="STORAGE_BACKEND"):
        load_settings(
            app_env="development",
            environ={"STORAGE_BACKEND": "filesystem"},
            dotenv_dir=tmp_path,
        )


def test_storage_settings_parse_typed_values_without_mutating_process_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_REGION", "process-region")
    settings = load_settings(
        app_env="test",
        environ={
            "STORAGE_REGION": "env-region",
            "STORAGE_ACCESS_KEY_ID": "access-key",
            "STORAGE_SECRET_ACCESS_KEY": "secret-key",
            "STORAGE_SIGNING_SECRET": "signing-secret",
            "STORAGE_URL_EXPIRY": "600",
            "STORAGE_RETENTION_DAYS": "45",
        },
        dotenv_dir=tmp_path,
    )

    assert settings.storage_region == "env-region"
    assert settings.storage_access_key_id == "access-key"
    assert settings.storage_secret_access_key == "secret-key"
    assert settings.storage_signing_secret == "signing-secret"
    assert settings.storage_url_expiry == 600
    assert settings.storage_retention_days == 45
    assert os.environ["STORAGE_REGION"] == "process-region"

    serialized = settings.to_dict()
    assert serialized["storage_access_key_id"] == "<redacted>"
    assert serialized["storage_secret_access_key"] == "<redacted>"
    assert serialized["storage_signing_secret"] == "<redacted>"
    assert "secret-key" not in repr(settings)
    assert "signing-secret" not in repr(settings)
    assert "signing-secret" not in repr(serialized)


def test_production_storage_backend_requires_s3(tmp_path):
    with pytest.raises(ConfigurationError, match="STORAGE_BACKEND"):
        load_settings(
            app_env="production",
            environ={
                "STORAGE_BACKEND": "local",
                "OPENAI_API_KEY": "offline-key",
                "OPENAI_BASE_URL": "https://offline.invalid",
                "OPENAI_MODEL": "offline-model",
                "DATABASE_URL": "mysql+pymysql://user:password@db.example.invalid/db",
            },
            dotenv_dir=tmp_path,
        )


def test_production_s3_backend_requires_endpoint_and_bucket(tmp_path):
    with pytest.raises(ConfigurationError, match="STORAGE_ENDPOINT|STORAGE_BUCKET"):
        load_settings(
            app_env="production",
            environ={
                "OPENAI_API_KEY": "offline-key",
                "OPENAI_BASE_URL": "https://offline.invalid",
                "OPENAI_MODEL": "offline-model",
                "DATABASE_URL": "mysql+pymysql://user:password@db.example.invalid/db",
            },
            dotenv_dir=tmp_path,
        )


@pytest.mark.parametrize("key", ["STORAGE_URL_EXPIRY", "STORAGE_RETENTION_DAYS"])
def test_storage_positive_integer_settings_reject_invalid_values(tmp_path, key):
    with pytest.raises(ConfigurationError, match=key):
        load_settings(
            app_env="test",
            environ={key: "0"},
            dotenv_dir=tmp_path,
        )


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


def test_settings_derives_controlled_local_storage_root(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={"DATABASE_URL": "sqlite:///:memory:"},
        dotenv_dir=tmp_path,
    )

    assert settings.storage_local_root == Path("outputs/test/datasets")


def test_settings_accepts_explicit_storage_local_root(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "DATABASE_URL": "sqlite:///:memory:",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "uploads"),
        },
        dotenv_dir=tmp_path,
    )

    assert settings.storage_local_root == tmp_path / "uploads"


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


def test_production_factory_accepts_provider_default_credentials(tmp_path, monkeypatch):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "DATABASE_URL": (
                "mysql+pymysql://user:password@db.example.invalid:3306/"
                "data_analysis"
            ),
            "REDIS_URL": "redis://redis.example.invalid:6379/0",
            "STORAGE_ENDPOINT": "https://storage.example.invalid",
            "STORAGE_BUCKET": "data-analysis",
        },
        dotenv_dir=tmp_path,
    )

    fake_boto3 = type("FakeBoto3", (), {})()
    fake_boto3.client = lambda service_name, **kwargs: object()
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    storage = build_storage(settings)
    assert storage.__class__.__name__ == "S3Storage"


def test_production_whitespace_database_url_is_missing(tmp_path):
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(
            app_env="production",
            environ={
                "OPENAI_API_KEY": "offline-key",
                "OPENAI_BASE_URL": "https://offline.invalid",
                "OPENAI_MODEL": "offline-model",
                "DATABASE_URL": " \t\n",
                "REDIS_URL": "redis://redis.example.invalid:6379/0",
            },
            dotenv_dir=tmp_path,
        )


def test_production_sqlite_database_url_is_rejected_at_settings_boundary(tmp_path):
    with pytest.raises(ConfigurationError, match="DATABASE_URL|MySQL"):
        load_settings(
            app_env="production",
            environ={
                "OPENAI_API_KEY": "offline-key",
                "OPENAI_BASE_URL": "https://offline.invalid",
                "OPENAI_MODEL": "offline-model",
                "DATABASE_URL": f"sqlite:///{tmp_path / 'production.sqlite3'}",
                "REDIS_URL": "redis://redis.example.invalid:6379/0",
                "STORAGE_ENDPOINT": "https://storage.example.invalid",
                "STORAGE_BUCKET": "data-analysis",
            },
            dotenv_dir=tmp_path,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_TASK_RUNTIME", "0"),
        ("MAX_TASK_RUNTIME", "not-an-int"),
        ("MAX_UPLOAD_SIZE", "-1"),
        ("LOG_LEVEL", "verbose"),
        ("WORKER_RETRY_BACKOFF_SECONDS", "nan"),
        ("WORKER_RETRY_BACKOFF_SECONDS", "inf"),
    ],
)
def test_invalid_values_name_their_configuration_key(tmp_path, key, value):
    environ = {
        "OPENAI_API_KEY": "offline-key",
        "OPENAI_BASE_URL": "https://offline.invalid",
        "OPENAI_MODEL": "offline-model",
        "DATABASE_URL": (
            "mysql+pymysql://user:password@db.example.invalid:3306/"
            "data_analysis"
        ),
        "REDIS_URL": "redis://redis.example.invalid:6379/0",
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
            "DATABASE_URL": (
                "mysql+pymysql://user:password@db.example.invalid:3306/"
                "data_analysis"
            ),
            "REDIS_URL": "redis://redis.example.invalid:6379/0",
            "STORAGE_ENDPOINT": "https://storage.example.invalid",
            "STORAGE_BUCKET": "data-analysis",
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
