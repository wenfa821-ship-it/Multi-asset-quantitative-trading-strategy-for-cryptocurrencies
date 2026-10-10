"""Structured logging: human-readable on console, JSONL on disk (audit trail)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .util import iso

_LOGGER_NAME = "ta"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = iso()[11:19]
        base = f"{stamp} {record.levelname:<7} {record.getMessage()}"
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict) and extra:
            pairs = " ".join(f"{k}={v}" for k, v in extra.items())
            base = f"{base}  [{pairs}]"
        return base


def setup(log_dir: str | Path | None = None, level: str = "INFO", console: bool = True) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.propagate = False

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(ConsoleFormatter())
        logger.addHandler(stream)

    if log_dir is not None:
        target = Path(log_dir)
        target.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(target / "ta.jsonl", encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
