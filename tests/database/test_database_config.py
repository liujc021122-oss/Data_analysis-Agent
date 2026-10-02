from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.persistence.database import Database, UTCDateTimeMicrosecond
from data_analysis_agent.persistence.errors import DatabaseConfigurationError


def test_database_factory_names_missing_database_url():
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test"},
        dotenv_dir=Path(".") / "nonexistent-test-config",
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        Database.from_settings(settings)


def test_database_factory_accepts_sqlite_url(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'database.sqlite3'}",
        },
    )

    database = Database.from_settings(settings)

    assert database.engine.url.get_backend_name() == "sqlite"
    assert database.session_factory.kw["expire_on_commit"] is False


def test_database_factory_accepts_production_mysql_url_without_connecting():
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "STORAGE_ENDPOINT": "https://storage.invalid",
            "STORAGE_BUCKET": "offline-bucket",
            "STORAGE_SIGNING_SECRET": "signing-secret",
            "DATABASE_URL": (
                "mysql+pymysql://user:password@db.example.invalid:3306/"
                "data_analysis"
            ),
        },
    )

    database = Database.from_settings(settings)

    assert database.engine.url.get_backend_name() == "mysql"
    assert database.engine.url.drivername == "mysql+pymysql"


def test_mysql_datetime_type_preserves_microseconds():
    datetime_type = UTCDateTimeMicrosecond().load_dialect_impl(mysql.dialect())

    assert UTCDateTimeMicrosecond.__dict__.get("cache_ok") is True
    assert str(datetime_type.compile(dialect=mysql.dialect())) == "DATETIME(6)"


def test_database_factory_rejects_sqlite_url_for_production(tmp_path):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "STORAGE_ENDPOINT": "https://storage.invalid",
            "STORAGE_BUCKET": "offline-bucket",
            "STORAGE_SIGNING_SECRET": "signing-secret",
            "DATABASE_URL": (
                "mysql+pymysql://user:password@db.example.invalid:3306/"
                "data_analysis"
            ),
        },
    )
    settings = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'production.sqlite3'}",
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL|MySQL"):
        Database.from_settings(settings)


@pytest.mark.parametrize("database_url", ["not-a-url", "ftp://example.invalid/db"])
def test_database_factory_rejects_unsupported_database_url(database_url):
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test", "DATABASE_URL": database_url},
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        Database.from_settings(settings)
