"""Asynchronous logging pipeline for the firesky engine web service.

Pipeline (matches the project's required architecture)::

    root logger
        │  QueueHandler  (non-blocking, async)
        ▼
    QueueListener  (runs in its own thread)
        │
        ├──▶ TimedRotatingFileHandler  ──▶  JSON Formatter  ──▶  logs/app.log
        │                                                  logs/app.log.20260718
        │                                                  logs/app.log.20260717
        └──▶ StreamHandler (console, optional, human-readable)

The queue keeps request handlers from blocking on disk I/O: records are
handed off instantly and drained by the listener thread.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # uvicorn is a hard dependency at runtime, but keep import soft for tools
    import uvicorn

    _UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")
except Exception:  # pragma: no cover
    _UVICORN_LOGGERS = ()


# Attributes that belong to the LogRecord machinery itself — never mirror these
# into the JSON payload as extra fields.
_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects (UTC timestamps)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # Surface any structured fields attached via `extra={...}`.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload.setdefault(key, value)
        return json.dumps(payload, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """Compact, human-readable line for local development console output."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        extra = ""
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            extra += f" {key}={value}"
        line = f"{ts} {record.levelname:<5} {record.name} {record.getMessage()}"
        if extra:
            line += " |" + extra
        return line


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: str | Path = "logs",
    app_name: str = "app",
    when: str = "midnight",
    backup_count: int = 14,
    console: bool = True,
    capture_uvicorn: bool = True,
) -> logging.handlers.QueueListener:
    """Configure root logging to emit through an async queue to rotating JSON files.

    Returns the started :class:`QueueListener` so callers can ``stop()`` it on
    shutdown. Idempotent enough to call once at process start.
    """
    log_level = getattr(logging, str(level).upper(), logging.INFO)

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue(-1)
    queue_handler = logging.handlers.QueueHandler(log_queue)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_dir / f"{app_name}.log"),
        when=when,
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    # Produces names like app.log.20260718 (matches the requested layout).
    file_handler.suffix = "%Y%m%d"
    file_handler.setFormatter(JsonFormatter())
    file_handler.setLevel(log_level)

    handlers: list[logging.Handler] = [file_handler]

    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(_ConsoleFormatter())
        stream_handler.setLevel(log_level)
        handlers.append(stream_handler)

    listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True
    )
    listener.start()

    root.addHandler(queue_handler)
    root.setLevel(log_level)
    root.propagate = False

    if capture_uvicorn:
        for name in _UVICORN_LOGGERS:
            logger = logging.getLogger(name)
            logger.handlers = []
            logger.propagate = True
            logger.setLevel(log_level)

    return listener
