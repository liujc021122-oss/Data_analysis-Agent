import pytest

from data_analysis_agent.config.settings import ConfigurationError, load_settings


def test_development_worker_defaults_to_a_single_solo_process(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={},
        dotenv_dir=tmp_path,
    )

    assert settings.worker_pool == "solo"
    assert settings.worker_concurrency == 1


def test_production_worker_defaults_to_prefork_without_forcing_concurrency(tmp_path):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "DATABASE_URL": "mysql+pymysql://user:password@db.example.invalid/app",
            "REDIS_URL": "redis://redis.example.invalid:6379/0",
            "STORAGE_ENDPOINT": "https://storage.example.invalid",
            "STORAGE_BUCKET": "data-analysis",
            "STORAGE_SIGNING_SECRET": "signing-secret",
            "EXECUTION_BACKEND": "container",
        },
        dotenv_dir=tmp_path,
    )

    assert settings.worker_pool == "prefork"
    assert settings.worker_concurrency is None


def test_worker_pool_and_concurrency_can_be_overridden(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={"WORKER_POOL": "threads", "WORKER_CONCURRENCY": "3"},
        dotenv_dir=tmp_path,
    )

    assert settings.worker_pool == "threads"
    assert settings.worker_concurrency == 3


@pytest.mark.parametrize(
    ("key", "value"),
    [("WORKER_POOL", "unknown"), ("WORKER_CONCURRENCY", "0")],
)
def test_invalid_worker_runtime_settings_name_their_key(tmp_path, key, value):
    with pytest.raises(ConfigurationError, match=key):
        load_settings(
            app_env="development",
            environ={key: value},
            dotenv_dir=tmp_path,
        )
