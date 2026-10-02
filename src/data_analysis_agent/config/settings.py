from dataclasses import dataclass, field, fields
import math
import os
import re
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional, cast

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from .llm import LLMConfig


EnvironmentName = Literal["development", "test", "production"]
VALID_ENVIRONMENTS = {"development", "test", "production"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_EXECUTION_BACKENDS = {"local", "container"}
VALID_EXECUTION_NETWORK_MODES = {"none", "bridge"}
VALID_STORAGE_BACKENDS = {"local", "s3"}
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_TASK_RUNTIME = 900
DEFAULT_MAX_UPLOAD_SIZE = 104857600
DEFAULT_WORKER_MAX_RETRIES = 3
DEFAULT_WORKER_RETRY_BACKOFF_SECONDS = 5.0
DEFAULT_WORKER_STALE_AFTER_SECONDS = 1800
DEFAULT_SESSION_TTL_SECONDS = 86400
DEFAULT_SESSION_COOKIE_NAME = "daa_session"
LOGGER_NAME = "data_analysis_agent"


class ConfigurationError(ValueError):
    pass


def _nonblank(value):
    if value is None or not str(value).strip():
        return None
    return value


@dataclass(frozen=True)
class Settings:
    app_env: EnvironmentName
    database_url: Optional[str] = field(repr=False)
    redis_url: Optional[str] = field(repr=False)
    storage_endpoint: Optional[str]
    storage_bucket: Optional[str]
    storage_local_root: Path
    openai_api_key: Optional[str] = field(repr=False)
    openai_base_url: str
    openai_model: str
    max_task_runtime: int
    max_upload_size: int
    output_dir: Path
    log_level: str
    storage_backend: Literal["local", "s3"] = "local"
    storage_region: Optional[str] = None
    storage_access_key_id: Optional[str] = field(default=None, repr=False)
    storage_secret_access_key: Optional[str] = field(default=None, repr=False)
    storage_signing_secret: Optional[str] = field(default=None, repr=False)
    storage_url_expiry: int = 300
    storage_retention_days: int = 30
    execution_backend: Literal["local", "container"] = "local"
    execution_image: Optional[str] = None
    execution_network_mode: Literal["none", "bridge"] = "none"
    worker_max_retries: int = DEFAULT_WORKER_MAX_RETRIES
    worker_retry_backoff_seconds: float = DEFAULT_WORKER_RETRY_BACKOFF_SECONDS
    worker_stale_after_seconds: int = DEFAULT_WORKER_STALE_AFTER_SECONDS
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    session_cookie_name: str = DEFAULT_SESSION_COOKIE_NAME
    session_cookie_secure: bool = False
    auth_admin_emails: tuple[str, ...] = ()

    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider="deepseek",
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            model=self.openai_model,
        )

    def to_dict(self) -> dict[str, object]:
        secret_fields = {
            "database_url",
            "redis_url",
            "openai_api_key",
            "storage_access_key_id",
            "storage_secret_access_key",
            "storage_signing_secret",
        }
        serialized: dict[str, object] = {}
        for setting in fields(self):
            value = getattr(self, setting.name)
            if setting.name in secret_fields and value is not None:
                value = "<redacted>"
            elif isinstance(value, Path):
                value = str(value)
            serialized[setting.name] = value
        return serialized


def _get_environment(raw: str) -> EnvironmentName:
    if raw not in VALID_ENVIRONMENTS:
        raise ConfigurationError(
            "APP_ENV must be one of development, test, or production"
        )
    return cast(EnvironmentName, raw)


def _storage_backend(
    values: Mapping[str, str], environment: EnvironmentName
) -> Literal["local", "s3"]:
    selected = (
        _nonblank(values.get("STORAGE_BACKEND"))
        or ("s3" if environment == "production" else "local")
    ).lower()
    if selected not in VALID_STORAGE_BACKENDS:
        raise ConfigurationError("STORAGE_BACKEND must be one of local or s3")
    if environment == "production" and selected != "s3":
        raise ConfigurationError("STORAGE_BACKEND must be s3 in production")
    return cast(Literal["local", "s3"], selected)


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{key} must be a positive integer")
    return parsed


def _nonnegative_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a non-negative integer") from exc
    if parsed < 0:
        raise ConfigurationError(f"{key} must be a non-negative integer")
    return parsed


def _nonnegative_float(
    values: Mapping[str, str], key: str, default: float
) -> float:
    raw_value = values.get(key, str(default))
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ConfigurationError(f"{key} must be a non-negative number")
    return parsed


def _strict_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw_value = values.get(key)
    if raw_value is None or not str(raw_value).strip():
        return default
    normalized = str(raw_value).strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"{key} must be true or false")
    return normalized == "true"


def _cookie_name(values: Mapping[str, str]) -> str:
    value = _nonblank(
        values.get("AUTH_SESSION_COOKIE_NAME", DEFAULT_SESSION_COOKIE_NAME)
    )
    if value is None or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
        raise ConfigurationError(
            "AUTH_SESSION_COOKIE_NAME must be a nonblank ASCII token"
        )
    return str(value)


def _admin_emails(values: Mapping[str, str]) -> tuple[str, ...]:
    raw = _nonblank(values.get("AUTH_ADMIN_EMAILS"))
    if raw is None:
        return ()
    emails = tuple(item.strip().lower() for item in str(raw).split(","))
    if any(not item or item.count("@") != 1 for item in emails):
        raise ConfigurationError("AUTH_ADMIN_EMAILS must contain valid emails")
    return emails


def _validate_production_database_url(database_url: str) -> None:
    try:
        backend = make_url(database_url).get_backend_name()
    except Exception as exc:
        raise ConfigurationError(
            "DATABASE_URL must use MySQL in production"
        ) from exc
    if backend != "mysql":
        raise ConfigurationError("DATABASE_URL must use MySQL in production")


def _read_values(
    environment: EnvironmentName,
    environ: Mapping[str, str],
    dotenv_dir: Path,
) -> Dict[str, str]:
    values: Dict[str, str] = {}
    selected = dotenv_dir / f".env.{environment}"
    if selected.is_file():
        values.update(
            {
                key: value
                for key, value in dotenv_values(selected).items()
                if value is not None
            }
        )
    elif environment == "development":
        fallback = dotenv_dir / ".env"
        if fallback.is_file():
            values.update(
                {
                    key: value
                    for key, value in dotenv_values(fallback).items()
                    if value is not None
                }
            )
    values.update({key: value for key, value in environ.items() if value is not None})
    return values


def load_settings(
    app_env: EnvironmentName | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_dir: Path | None = None,
    output_dir: str | Path | None = None,
) -> Settings:
    provided = environ if environ is not None else os.environ
    environment = _get_environment(
        app_env or provided.get("APP_ENV", "development")
    )
    values = _read_values(
        environment, provided, Path(dotenv_dir or Path.cwd())
    )
    defaults = {
        "development": "outputs",
        "test": "outputs/test",
        "production": "outputs/production",
    }
    selected_output = (
        _nonblank(output_dir)
        or _nonblank(values.get("OUTPUT_DIR"))
        or defaults[environment]
    )
    selected_output_path = Path(selected_output)
    storage_backend = _storage_backend(values, environment)
    api_key = _nonblank(values.get("OPENAI_API_KEY"))
    base_url = _nonblank(values.get("OPENAI_BASE_URL"))
    model = _nonblank(values.get("OPENAI_MODEL"))
    execution_backend = (
        _nonblank(values.get("EXECUTION_BACKEND"))
        or ("container" if environment == "production" else "local")
    ).lower()
    if execution_backend not in VALID_EXECUTION_BACKENDS:
        raise ConfigurationError(
            "EXECUTION_BACKEND must be one of local or container"
        )
    execution_network_mode = (
        _nonblank(values.get("EXECUTION_NETWORK_MODE"))
        or _nonblank(values.get("EXECUTION_NETWORK_POLICY"))
        or "none"
    ).lower()
    execution_network_mode = {
        "disabled": "none",
        "enabled": "bridge",
    }.get(execution_network_mode, execution_network_mode)
    if execution_network_mode not in VALID_EXECUTION_NETWORK_MODES:
        raise ConfigurationError(
            "EXECUTION_NETWORK_MODE must be one of none or bridge"
        )
    session_cookie_secure = _strict_bool(
        values,
        "AUTH_SESSION_COOKIE_SECURE",
        environment == "production",
    )
    if environment == "production" and not session_cookie_secure:
        raise ConfigurationError(
            "AUTH_SESSION_COOKIE_SECURE must be true in production"
        )
    settings = Settings(
        app_env=environment,
        database_url=_nonblank(values.get("DATABASE_URL")),
        redis_url=_nonblank(values.get("REDIS_URL")),
        storage_endpoint=_nonblank(values.get("STORAGE_ENDPOINT")),
        storage_bucket=_nonblank(values.get("STORAGE_BUCKET")),
        storage_region=_nonblank(values.get("STORAGE_REGION")),
        storage_access_key_id=_nonblank(values.get("STORAGE_ACCESS_KEY_ID")),
        storage_secret_access_key=_nonblank(
            values.get("STORAGE_SECRET_ACCESS_KEY")
        ),
        storage_signing_secret=_nonblank(values.get("STORAGE_SIGNING_SECRET")),
        storage_url_expiry=_positive_int(
            values, "STORAGE_URL_EXPIRY", 300
        ),
        storage_retention_days=_positive_int(
            values, "STORAGE_RETENTION_DAYS", 30
        ),
        openai_api_key=api_key,
        openai_base_url=base_url or (DEFAULT_BASE_URL if environment != "production" else ""),
        openai_model=model or (DEFAULT_MODEL if environment != "production" else ""),
        max_task_runtime=_positive_int(
            values, "MAX_TASK_RUNTIME", DEFAULT_MAX_TASK_RUNTIME
        ),
        max_upload_size=_positive_int(
            values, "MAX_UPLOAD_SIZE", DEFAULT_MAX_UPLOAD_SIZE
        ),
        output_dir=selected_output_path,
        storage_local_root=Path(
            _nonblank(values.get("STORAGE_LOCAL_ROOT"))
            or selected_output_path / "datasets"
        ),
        log_level=values.get("LOG_LEVEL", "INFO").upper(),
        storage_backend=storage_backend,
        execution_backend=cast(Literal["local", "container"], execution_backend),
        execution_image=_nonblank(values.get("EXECUTION_IMAGE")),
        execution_network_mode=cast(
            Literal["none", "bridge"], execution_network_mode
        ),
        worker_max_retries=_nonnegative_int(
            values, "WORKER_MAX_RETRIES", DEFAULT_WORKER_MAX_RETRIES
        ),
        worker_retry_backoff_seconds=_nonnegative_float(
            values,
            "WORKER_RETRY_BACKOFF_SECONDS",
            DEFAULT_WORKER_RETRY_BACKOFF_SECONDS,
        ),
        worker_stale_after_seconds=_positive_int(
            values,
            "WORKER_STALE_AFTER_SECONDS",
            DEFAULT_WORKER_STALE_AFTER_SECONDS,
        ),
        session_ttl_seconds=_positive_int(
            values, "AUTH_SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS
        ),
        session_cookie_name=_cookie_name(values),
        session_cookie_secure=session_cookie_secure,
        auth_admin_emails=_admin_emails(values),
    )
    if settings.log_level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, or ERROR"
        )
    if environment == "production":
        if settings.execution_backend != "container":
            raise ConfigurationError(
                "EXECUTION_BACKEND must be container in production"
            )
        missing = [
            field
            for field, value in (
                ("OPENAI_API_KEY", settings.openai_api_key),
                ("OPENAI_BASE_URL", settings.openai_base_url),
                ("OPENAI_MODEL", settings.openai_model),
                ("DATABASE_URL", settings.database_url),
                *((
                    ("STORAGE_ENDPOINT", settings.storage_endpoint),
                    ("STORAGE_BUCKET", settings.storage_bucket),
                    ("STORAGE_SIGNING_SECRET", settings.storage_signing_secret),
                ) if settings.storage_backend == "s3" else ()),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required production configuration: " + ", ".join(missing)
            )
        assert settings.database_url is not None
        _validate_production_database_url(settings.database_url)
    return settings


from .logging import configure_logging
