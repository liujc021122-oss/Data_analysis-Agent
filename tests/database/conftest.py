from pathlib import Path

import pytest

from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.unit_of_work import UnitOfWork
from data_analysis_agent.config.settings import load_settings


def settings_for(database_url: str):
    return load_settings(
        app_env="test",
        environ={"APP_ENV": "test", "DATABASE_URL": database_url},
    )


@pytest.fixture
def engine(tmp_path: Path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'schema.sqlite3'}",
        },
    )
    database = Database.from_settings(settings)
    db_engine = database.engine
    init_database(db_engine)
    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def uow_factory(tmp_path: Path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'persistence.sqlite3'}",
        },
    )
    database = Database.from_settings(settings)
    init_database(database.engine)
    try:
        yield lambda: UnitOfWork(database.session_factory)
    finally:
        database.engine.dispose()
