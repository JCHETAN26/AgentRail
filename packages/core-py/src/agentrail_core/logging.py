"""Structured JSON logging with correlation binding and redaction.

Rules enforced here (see ``docs/security/THREAT_MODEL.md``):

* logs are JSON so they can be shipped without a fragile regex parser;
* correlation and trace identifiers are attached automatically;
* values whose key looks sensitive are replaced with ``"[redacted]"`` before
  serialisation, so a careless ``extra={"api_key": ...}`` cannot leak a secret.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from agentrail_core.correlation import current_context

REDACTED = "[redacted]"

#: Substrings that mark a field as sensitive. Matching is case-insensitive and
#: substring-based so that ``github_webhook_secret`` and ``authorization`` are
#: both caught.
SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "prompt",
    "secret",
    "session",
    "token",
)

_RESERVED_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys in a log payload."""
    if _depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            key: REDACTED if is_sensitive_key(str(key)) else redact(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, _depth=_depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single-line JSON object."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "environment": self._environment,
            "message": record.getMessage(),
        }

        context = current_context()
        if context is not None:
            payload.update(context.to_log_fields())

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload.update(redact(extras))

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(*, service: str, environment: str, level: str = "INFO") -> None:
    """Install the JSON formatter as the sole root handler.

    Safe to call more than once; existing handlers are replaced so that a
    reloaded uvicorn worker does not duplicate every line.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; force its loggers through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
