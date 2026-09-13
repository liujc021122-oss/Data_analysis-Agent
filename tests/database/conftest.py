from pathlib import Path

import pytest

from data_analysis_agent.persistence.database import create_engine_from_settings
from data_analysis_agent.config.settings import load_settings


@pytest.fixture
def engine(tmp_path: Path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'schema.sqlite3'}",
        },
    )
    db_engine = create_engine_from_settings(settings)
    try:
        yield db_engine
    finally:
        db_engine.dispose()
