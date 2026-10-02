from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import CHAR, DateTime, create_engine, event
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from ..config.settings import Settings
from .errors import DatabaseConfigurationError


class Base(DeclarativeBase):
    pass


class UUIDString(TypeDecorator[UUID]):
    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value if isinstance(value, UUID) else UUID(str(value)))

    def process_result_value(self, value, dialect):
        return UUID(value) if value is not None else None


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class UTCDateTimeMicrosecond(UTCDateTime):
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())


@dataclass(frozen=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        engine = create_engine_from_settings(settings)
        return cls(engine, create_session_factory(engine))


def create_engine_from_settings(settings: Settings) -> Engine:
    raw_url = settings.database_url
    if raw_url is None or not raw_url.strip():
        raise DatabaseConfigurationError("DATABASE_URL is required for persistence")
    try:
        url = make_url(raw_url)
        if url.get_backend_name() not in {"sqlite", "mysql"}:
            raise DatabaseConfigurationError(
                "DATABASE_URL must use sqlite or mysql backend"
            )
        if settings.app_env == "production" and url.get_backend_name() != "mysql":
            raise DatabaseConfigurationError(
                "DATABASE_URL must use MySQL in production"
            )
        engine_options: dict[str, object] = {
            "future": True,
            "pool_pre_ping": True,
        }
        if url.get_backend_name() == "mysql":
            engine_options["connect_args"] = {"connect_timeout": 3}
        engine = create_engine(url, **engine_options)
        if url.get_backend_name() == "sqlite":
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    except DatabaseConfigurationError:
        raise
    except Exception as exc:
        raise DatabaseConfigurationError(f"Invalid DATABASE_URL: {raw_url}") from exc


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    from . import orm_models  # noqa: F401  # register mapped tables before create_all

    Base.metadata.create_all(engine)
