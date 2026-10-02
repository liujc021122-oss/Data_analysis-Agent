import sys
from types import SimpleNamespace

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.worker.cli import build_parser, main
from data_analysis_agent.worker.health import worker_healthcheck


def test_worker_healthcheck_succeeds_when_dependencies_are_ready(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
            "REDIS_URL": "redis://localhost:6379/0",
        },
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.database_ready",
        lambda configured: True,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.redis_ready",
        lambda configured: True,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.storage_ready",
        lambda configured: True,
    )

    assert worker_healthcheck(settings) is True


def test_worker_healthcheck_fails_when_redis_is_unavailable(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
            "REDIS_URL": "redis://localhost:6379/0",
        },
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.database_ready",
        lambda configured: True,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.redis_ready",
        lambda configured: False,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.storage_ready",
        lambda configured: True,
    )

    assert worker_healthcheck(settings) is False


def test_worker_healthcheck_fails_when_storage_is_unavailable(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
            "REDIS_URL": "redis://localhost:6379/0",
        },
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.database_ready",
        lambda configured: True,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.redis_ready",
        lambda configured: True,
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.storage_ready",
        lambda configured: False,
    )

    assert worker_healthcheck(settings) is False


def test_storage_ready_runs_the_configured_storage_healthcheck(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={"DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}"},
    )
    calls = []

    class FakeStorage:
        def healthcheck(self):
            calls.append("healthcheck")

    monkeypatch.setattr(
        "data_analysis_agent.worker.health.build_storage",
        lambda configured: FakeStorage(),
    )

    from data_analysis_agent.worker.health import storage_ready

    assert storage_ready(settings) is True
    assert calls == ["healthcheck"]


def test_worker_parser_accepts_healthcheck_flag():
    args = build_parser().parse_args(["--healthcheck"])

    assert args.healthcheck is True


def test_worker_healthcheck_help_mentions_object_storage():
    assert "object storage" in build_parser().format_help()


def test_worker_healthcheck_main_returns_one_without_starting_celery(monkeypatch):
    settings = SimpleNamespace()
    monkeypatch.setattr("data_analysis_agent.worker.cli.load_settings", lambda **_: settings)
    monkeypatch.setattr("data_analysis_agent.worker.cli.configure_logging", lambda _: None)
    monkeypatch.setattr("data_analysis_agent.worker.cli.worker_healthcheck", lambda _: False)
    monkeypatch.setattr(
        "data_analysis_agent.worker.cli.build_celery_app",
        lambda _: (_ for _ in ()).throw(AssertionError("Celery should not start")),
    )

    assert main(["--healthcheck"]) == 1


def test_database_ready_disposes_engine_after_query_failure(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={"DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}"},
    )
    disposed = []

    class FailingConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            raise RuntimeError("database unavailable")

    fake_database = SimpleNamespace(
        engine=SimpleNamespace(
            connect=lambda: FailingConnection(),
            dispose=lambda: disposed.append(True),
        )
    )
    monkeypatch.setattr(
        "data_analysis_agent.worker.health.Database.from_settings",
        lambda configured: fake_database,
    )

    from data_analysis_agent.worker.health import database_ready

    assert database_ready(settings) is False
    assert disposed == [True]


def test_redis_ready_uses_bounded_timeouts_and_closes_client(monkeypatch, tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
            "REDIS_URL": "redis://localhost:6379/0",
        },
    )
    calls = []

    class FakeRedis:
        @staticmethod
        def from_url(url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(ping=lambda: True, close=lambda: calls.append("closed"))

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedis))

    from data_analysis_agent.worker.health import redis_ready

    assert redis_ready(settings) is True
    assert calls == [
        (
            "redis://localhost:6379/0",
            {"socket_connect_timeout": 1, "socket_timeout": 1},
        ),
        "closed",
    ]
