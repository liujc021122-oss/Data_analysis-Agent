from __future__ import annotations

from sqlalchemy import text

from ..config.settings import Settings
from ..persistence.database import Database


def database_ready(settings: Settings) -> bool:
    database = None
    try:
        database = Database.from_settings(settings)
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        if database is not None:
            database.engine.dispose()


def redis_ready(settings: Settings) -> bool:
    if not settings.redis_url:
        return False

    client = None
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def worker_healthcheck(settings: Settings) -> bool:
    database_ok = database_ready(settings)
    redis_ok = redis_ready(settings)
    return database_ok and redis_ok


__all__ = ["database_ready", "redis_ready", "worker_healthcheck"]
