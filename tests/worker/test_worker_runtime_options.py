from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from data_analysis_agent.worker import cli
from data_analysis_agent.domain.models import AnalysisTask
from data_analysis_agent.persistence.database import Database, init_database
from data_analysis_agent.persistence.models import UserRecord
from data_analysis_agent.persistence.unit_of_work import UnitOfWork
from data_analysis_agent.config.settings import load_settings


def test_worker_parser_accepts_pool_and_concurrency_options():
    args = cli.build_parser().parse_args(
        ["--pool", "solo", "--concurrency", "1"]
    )

    assert args.pool == "solo"
    assert args.concurrency == 1


def test_worker_main_passes_configured_pool_and_concurrency(monkeypatch):
    calls = []
    settings = SimpleNamespace(worker_pool="solo", worker_concurrency=1)
    database = SimpleNamespace(
        engine=SimpleNamespace(dispose=lambda: calls.append("dispose"))
    )
    celery_app = SimpleNamespace(
        worker_main=lambda args: calls.append(("worker_main", args))
    )

    monkeypatch.setattr(cli, "load_settings", lambda **_: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda _: None)
    monkeypatch.setattr(cli.Database, "from_settings", lambda _: database)
    monkeypatch.setattr(cli, "build_celery_app", lambda _: celery_app)
    monkeypatch.setattr(
        cli,
        "build_celery_broker",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(cli, "build_worker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "register_analysis_task", lambda *_args: None)

    assert cli.main(["--loglevel", "INFO"]) == 0
    assert ("worker_main", [
        "worker",
        "--pool",
        "solo",
        "--concurrency",
        "1",
        "--loglevel",
        "INFO",
    ]) in calls
    assert calls[-1] == "dispose"


def test_build_worker_wires_database_backed_artifact_storage(monkeypatch, tmp_path):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cli, "DataAnalysisAgent", FakeAgent)
    owner_id = uuid4()
    task_id = uuid4()
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'worker.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
        dotenv_dir=tmp_path,
    )
    database = Database.from_settings(settings)
    init_database(database.engine)
    try:
        task = AnalysisTask(task_id=task_id, query="store artifacts")
        with UnitOfWork(database.session_factory) as uow:
            uow.users.ensure(
                UserRecord(user_id=owner_id, created_at=datetime.now(timezone.utc))
            )
            uow.tasks.add(
                user_id=owner_id,
                task=task,
                idempotency_key="artifact-wiring",
                request_hash="artifact-wiring-hash",
            )
            uow.commit()

        worker = cli.build_worker(settings, database, object())
        worker.agent_factory(task=task, user_id=owner_id)

        source = Path(tmp_path / "trend.png")
        source.write_bytes(b"chart")
        record = captured["artifact_storage"].store_file(
            task_id=task_id,
            source_path=source,
            artifact_type="CHART",
            filename="trend.png",
            mime_type="image/png",
        )

        assert captured["storage"] is not None
        with UnitOfWork(database.session_factory) as uow:
            assert uow.artifacts.get(record.artifact_id) == record
    finally:
        database.engine.dispose()
