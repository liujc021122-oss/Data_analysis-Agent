from __future__ import annotations

import argparse
import sys
from typing import Sequence
from uuid import UUID

from ..agent.core import DataAnalysisAgent
from ..config import build_storage
from ..config.settings import (
    VALID_WORKER_POOLS,
    ConfigurationError,
    configure_logging,
    load_settings,
)
from ..datasets import DatasetResolver, UnitOfWorkDatasetStore
from ..persistence.database import Database
from ..persistence.unit_of_work import UnitOfWork
from ..services.persistence import TaskPersistenceService
from ..storage import ArtifactStorageService
from .broker import CeleryTaskBroker
from .celery_app import build_celery_app, build_celery_broker, register_analysis_task
from .errors import WorkerConfigurationError
from .health import worker_healthcheck
from .worker import AnalysisTaskWorker


def _positive_int_argument(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-analysis-agent-worker",
        description="Run the data analysis Celery worker",
    )
    parser.add_argument(
        "--env", choices=("development", "test", "production"), default=None
    )
    parser.add_argument("--loglevel", default=None)
    parser.add_argument("--pool", choices=sorted(VALID_WORKER_POOLS), default=None)
    parser.add_argument("--concurrency", type=_positive_int_argument, default=None)
    parser.add_argument("--recover-stale", action="store_true")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="check the worker database, Redis, and object storage dependencies and exit",
    )
    return parser


class _PersistentArtifactRepository:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    def add(self, record):
        with self._uow_factory() as uow:
            return uow.artifacts.add(record)


class _PersistentReportRepository:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    def add(self, record):
        with self._uow_factory() as uow:
            return uow.reports.add(record)


def build_worker(settings, database: Database, broker: CeleryTaskBroker):
    storage = build_storage(settings)
    uow_factory = lambda: UnitOfWork(database.session_factory)
    dataset_store = UnitOfWorkDatasetStore(
        uow_factory
    )
    artifact_storage = ArtifactStorageService(
        storage=storage,
        artifact_repository=_PersistentArtifactRepository(uow_factory),
        report_repository=_PersistentReportRepository(uow_factory),
    )
    resolver = DatasetResolver(storage=storage, metadata_store=dataset_store)
    persistence = TaskPersistenceService(
        lambda: UnitOfWork(database.session_factory)
    )

    def agent_factory(
        *,
        task,
        user_id: UUID | None = None,
        on_transition=None,
    ):
        return DataAnalysisAgent(
            llm_config=settings.llm_config(),
            output_dir=str(settings.output_dir),
            max_rounds=task.max_rounds,
            dataset_resolver=resolver,
            dataset_owner_id=user_id,
            storage=storage,
            artifact_storage=artifact_storage,
            task_id=task.task_id,
            settings=settings,
            transition_callback=on_transition,
        )

    return AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=agent_factory,
        max_retries=settings.worker_max_retries,
        retry_backoff_seconds=settings.worker_retry_backoff_seconds,
        stale_after_seconds=settings.worker_stale_after_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = None
    try:
        settings = load_settings(app_env=args.env)
        configure_logging(settings)
        if args.healthcheck:
            return 0 if worker_healthcheck(settings) else 1
        database = Database.from_settings(settings)
        app = build_celery_app(settings)
        broker = build_celery_broker(settings, celery_app=app)
        worker = build_worker(settings, database, broker)
        if args.recover_stale:
            worker.recover_stale()
        register_analysis_task(app, lambda: worker)
        worker_args = ["worker", "--pool", args.pool or settings.worker_pool]
        concurrency = (
            args.concurrency
            if args.concurrency is not None
            else settings.worker_concurrency
        )
        if concurrency is not None:
            worker_args.extend(["--concurrency", str(concurrency)])
        if args.loglevel:
            worker_args.extend(["--loglevel", args.loglevel])
        app.worker_main(worker_args)
        return 0
    except (ConfigurationError, WorkerConfigurationError) as exc:
        print(f"Worker configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if database is not None:
            database.engine.dispose()


__all__ = ["build_parser", "build_worker", "main"]
