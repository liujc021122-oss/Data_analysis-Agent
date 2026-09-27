from __future__ import annotations

from typing import Any

from ..config.settings import Settings, load_settings
from .broker import CeleryTaskBroker
from .errors import WorkerConfigurationError


CELERY_TASK_NAME = "data_analysis_agent.worker.execute_analysis_task"


def build_celery_app(settings: Settings | None = None):
    settings = settings or load_settings()
    if not settings.redis_url:
        raise WorkerConfigurationError(
            "REDIS_URL is required to start the Celery worker"
        )
    try:
        from celery import Celery
    except ImportError as exc:
        raise WorkerConfigurationError(
            "Celery is not installed; install the worker extra"
        ) from exc
    app = Celery(
        "data_analysis_agent",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
    )
    return app


def build_celery_broker(
    settings: Settings | None = None,
    *,
    celery_app: Any | None = None,
) -> CeleryTaskBroker:
    app = celery_app or build_celery_app(settings)
    return CeleryTaskBroker(app, task_name=CELERY_TASK_NAME)


def register_analysis_task(celery_app: Any, worker_factory: Any):
    """Register a late-ack task without importing Celery at module import time."""

    @celery_app.task(
        bind=True,
        name=CELERY_TASK_NAME,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def execute_analysis_task(self, task_id: str):
        from uuid import UUID

        worker = worker_factory()
        return worker.process(UUID(task_id)).model_dump(mode="json")

    return execute_analysis_task


__all__ = [
    "CELERY_TASK_NAME",
    "build_celery_app",
    "build_celery_broker",
    "register_analysis_task",
]
