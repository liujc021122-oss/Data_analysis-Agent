from pathlib import Path

import pytest

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.persistence.database import Database
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


@pytest.mark.parametrize("database_url", ["not-a-url", "ftp://example.invalid/db"])
def test_database_factory_rejects_unsupported_database_url(database_url):
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test", "DATABASE_URL": database_url},
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        Database.from_settings(settings)
