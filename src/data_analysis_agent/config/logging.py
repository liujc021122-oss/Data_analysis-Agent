from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import Settings


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in ("request_id", "task_id"):
            value = getattr(record, field_name, None)
            if isinstance(value, (str, int)) and value:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("data_analysis_agent")
    logger.setLevel(getattr(logging, settings.log_level))
    if not logger.handlers:
        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setFormatter(JsonLogFormatter())
    logger.propagate = True
    return logger
