from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional

from dotenv import dotenv_values

from .llm import LLMConfig


EnvironmentName = Literal["development", "test", "production"]
VALID_ENVIRONMENTS = {"development", "test", "production"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_TASK_RUNTIME = 900
DEFAULT_MAX_UPLOAD_SIZE = 104857600
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
    database_url: Optional[str]
    redis_url: Optional[str]
    storage_endpoint: Optional[str]
    storage_bucket: Optional[str]
    openai_api_key: Optional[str]
    openai_base_url: str
    openai_model: str
    max_task_runtime: int
    max_upload_size: int
    output_dir: Path
    log_level: str

    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider="deepseek",
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            model=self.openai_model,
        )


def _get_environment(raw: str) -> EnvironmentName:
    if raw not in VALID_ENVIRONMENTS:
        raise ConfigurationError(
            "APP_ENV must be one of development, test, or production"
        )
    return raw


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key, str(default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{key} must be a positive integer")
    return parsed


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
    api_key = _nonblank(values.get("OPENAI_API_KEY"))
    base_url = _nonblank(values.get("OPENAI_BASE_URL"))
    model = _nonblank(values.get("OPENAI_MODEL"))
    settings = Settings(
        app_env=environment,
        database_url=values.get("DATABASE_URL"),
        redis_url=values.get("REDIS_URL"),
        storage_endpoint=values.get("STORAGE_ENDPOINT"),
        storage_bucket=values.get("STORAGE_BUCKET"),
        openai_api_key=api_key,
        openai_base_url=base_url or (DEFAULT_BASE_URL if environment != "production" else ""),
        openai_model=model or (DEFAULT_MODEL if environment != "production" else ""),
        max_task_runtime=_positive_int(
            values, "MAX_TASK_RUNTIME", DEFAULT_MAX_TASK_RUNTIME
        ),
        max_upload_size=_positive_int(
            values, "MAX_UPLOAD_SIZE", DEFAULT_MAX_UPLOAD_SIZE
        ),
        output_dir=Path(selected_output),
        log_level=values.get("LOG_LEVEL", "INFO").upper(),
    )
    if settings.log_level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, or ERROR"
        )
    if environment == "production":
        missing = [
            field
            for field, value in (
                ("OPENAI_API_KEY", settings.openai_api_key),
                ("OPENAI_BASE_URL", settings.openai_base_url),
                ("OPENAI_MODEL", settings.openai_model),
                ("DATABASE_URL", settings.database_url),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required production configuration: " + ", ".join(missing)
            )
    return settings


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.log_level))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            )
        )
        logger.addHandler(handler)
    logger.propagate = True
    return logger
