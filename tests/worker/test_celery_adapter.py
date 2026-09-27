from types import SimpleNamespace
from uuid import uuid4

import pytest

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.worker import (
    CeleryTaskBroker,
    WorkerConfigurationError,
    build_celery_app,
)


class FakeControl:
    def __init__(self):
        self.calls = []

    def revoke(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeCelery:
    def __init__(self):
        self.sent = []
        self.control = FakeControl()

    def send_task(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return SimpleNamespace(id=kwargs.get("task_id"))


def test_celery_broker_sends_only_task_id_and_can_revoke():
    app = FakeCelery()
    broker = CeleryTaskBroker(app, task_name="analysis.execute")
    task_id = uuid4()

    message = broker.enqueue(task_id, countdown=3)
    broker.revoke(task_id)

    assert message.task_id == task_id
    assert app.sent == [
        (("analysis.execute",), {"args": [str(task_id)], "task_id": str(task_id), "countdown": 3})
    ]
    assert app.control.calls == [
        ((str(task_id),), {"terminate": True, "signal": "SIGTERM"})
    ]


def test_production_settings_require_redis_for_worker(tmp_path):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://offline.invalid",
            "OPENAI_MODEL": "offline-model",
            "DATABASE_URL": "mysql+pymysql://user:pass@db.invalid/app",
            "STORAGE_ENDPOINT": "https://storage.invalid",
            "STORAGE_BUCKET": "bucket",
        },
        dotenv_dir=tmp_path,
    )
    with pytest.raises(WorkerConfigurationError, match="REDIS_URL"):
        build_celery_app(settings)


def test_celery_app_requires_redis_url(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={"DATABASE_URL": f"sqlite:///{tmp_path / 'db.sqlite3'}"},
        dotenv_dir=tmp_path,
    )
    with pytest.raises(WorkerConfigurationError, match="REDIS_URL"):
        build_celery_app(settings)
