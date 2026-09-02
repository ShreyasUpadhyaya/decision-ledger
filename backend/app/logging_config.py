"""Structured JSON logging (spec §13).

Every log line is a single JSON object on one line — trivially grep-able and ready to be
shipped into any log aggregator without a custom parser. The formatter has no third-party
dependency; it wraps the stdlib ``logging`` module so the rest of the app just calls
``get_logger(__name__)`` and logs normally, optionally passing structured fields via
``extra={...}``.

Lifecycle events the app is expected to emit (spec §13):
    * request lifecycle  — ``decision.request`` / ``decision.response``
    * ruleset loading    — ``ruleset.loaded``
    * signal degradation — ``signal.degraded``
    * evaluator runs     — ``evaluator.run``
"""
import datetime
import json
import logging
import sys
from typing import Any

# Attributes present on every stdlib LogRecord; anything *not* in here that a caller
# attaches via ``extra={...}`` is treated as a structured field and merged into the line.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    """Renders a LogRecord as a compact single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any structured fields passed through ``extra={...}``.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger. Idempotent — safe to call at
    every startup without stacking duplicate handlers."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Replace whatever handlers exist (e.g. uvicorn's default) with our single JSON one.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Let uvicorn's access/error loggers propagate to root so they're JSON too.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
