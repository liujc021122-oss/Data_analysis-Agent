from pathlib import Path

import pytest

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.unit_of_work import UnitOfWork


@pytest.fixture
def uow_factory(tmp_path: Path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
        },
    )
    database = Database.from_settings(settings)
    init_database(database.engine)
    try:
        yield lambda: UnitOfWork(database.session_factory)
    finally:
        database.engine.dispose()
