from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..api.application import APIApplication

health_router = APIRouter()


def check_database(database) -> bool:
    if database is None:
        return False
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_redis(redis_url: str | None) -> bool:
    if not redis_url:
        return False
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        try:
            client.ping()
        finally:
            client.close()
        return True
    except Exception:
        return False


def check_storage(application: APIApplication) -> bool:
    if application.storage is None:
        return False
    try:
        application.storage.healthcheck()
        return True
    except Exception:
        return False


def check_readiness(application: APIApplication) -> tuple[bool, dict[str, str]]:
    statuses = {
        "database": "ok" if check_database(application.database) else "unavailable",
        "redis": "ok" if check_redis(application.settings.redis_url) else "unavailable",
        "storage": "ok" if check_storage(application) else "unavailable",
    }
    return all(status == "ok" for status in statuses.values()), statuses


@health_router.get("/health/live")
def live_probe() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
def ready_probe(request: Request) -> JSONResponse:
    ready, components = check_readiness(request.app.state.api_application)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "unavailable", "components": components},
    )
