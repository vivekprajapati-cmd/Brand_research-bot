"""Structured JSON logging for the Brand Research Bot.

Every log record is emitted as a single-line JSON object with ``level``,
``message`` and optional ``extra`` fields so Render's log stream can be
parsed and filtered easily.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional


class JsonFormatter(logging.Formatter):
    """Format log records as compact single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a logger that emits structured JSON to stdout."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = (level or "INFO").upper()
    numeric_level = getattr(logging, level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(numeric_level)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str, **extra: Optional[str]) -> None:
    """Emit a JSON log with extra structured fields."""
    extra["message"] = message
    logger.info(message, extra={"extra_fields": extra})
