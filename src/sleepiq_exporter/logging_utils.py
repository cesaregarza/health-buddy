"""Structured logging with recursive secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

SENSITIVE_KEY_PARTS = (
    "authorization",
    "body",
    "cookie",
    "credential",
    "database_url",
    "dsn",
    "email",
    "password",
    "response",
    "secret",
    "token",
)


def _sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


class Redactor:
    """Remove known secrets and fields whose names imply sensitive content."""

    def __init__(self, secret_values: Sequence[str] = ()) -> None:
        self._secret_values = tuple(value for value in secret_values if value)

    def clean(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and _sensitive_key(key):
            return "[REDACTED]"
        if isinstance(value, str):
            cleaned = value
            for secret in self._secret_values:
                cleaned = cleaned.replace(secret, "[REDACTED]")
            return cleaned
        if isinstance(value, Mapping):
            return {
                str(item_key): self.clean(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return [self.clean(item) for item in value]
        if isinstance(value, (datetime,)):
            return value.isoformat()
        return value


class JsonLogFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        event_fields = getattr(record, "event_fields", {})
        if isinstance(event_fields, Mapping):
            payload.update(event_fields)
        return json.dumps(
            self._redactor.clean(payload),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


class TextLogFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        fields = self._redactor.clean(getattr(record, "event_fields", {}))
        suffix = ""
        if fields:
            suffix = " " + json.dumps(fields, sort_keys=True, default=str)
        event = self._redactor.clean(record.getMessage())
        return f"{record.levelname} {event}{suffix}"


def configure_logging(
    level: str,
    log_format: str,
    *,
    secret_values: Sequence[str] = (),
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the exporter logger and suppress sensitive upstream messages."""

    logger = logging.getLogger("sleepiq_exporter")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(level)

    handler = logging.StreamHandler(stream or sys.stderr)
    redactor = Redactor(secret_values)
    handler.setFormatter(
        JsonLogFormatter(redactor)
        if log_format == "json"
        else TextLogFormatter(redactor)
    )
    logger.addHandler(handler)

    # asyncsleepiq can include bed names or upstream response text in errors.
    logging.getLogger("ASyncSleepIQ").setLevel(logging.CRITICAL)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(level, event, extra={"event_fields": fields})
