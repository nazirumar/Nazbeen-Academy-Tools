"""Structured logging with request correlation.

Two formats are supported:
- ``json``   : one JSON object per line (production / log collectors)
- ``console``: human-readable colored lines (local development)

Every log record carries ``request_id`` when handling an HTTP request,
allowing end-to-end correlation between a client error report and the
server-side log lines (the id is also returned in the ``X-Request-ID``
response header and in the API envelope ``meta``).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_RESERVED_LOGRECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    """Minimal dependency-free JSON lines formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOGRECORD_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly formatter for local development."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
        base = (
            f"{timestamp:%H:%M:%S.%f} {color}{record.levelname:<8}{self.RESET} "
            f"[{record.name}] {record.getMessage()}"
        )
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOGRECORD_ATTRS and not key.startswith("_")
        }
        if extras:
            base += f" | {extras}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure the root logger and route stdlib/uvicorn logs through it."""

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ConsoleFormatter() if log_format == "console" else JsonFormatter()
    )
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn's loggers through the root handler for consistent output.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # Lower the noise of very chatty libraries.
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
