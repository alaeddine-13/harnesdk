"""Logging helpers for harnesdk modules."""

from __future__ import annotations

import logging
import os

from rich.logging import RichHandler


def _resolve_log_level(value: str) -> int:
    """Translate a log-level string to a `logging` level."""
    level = getattr(logging, value.upper(), None)
    return level if isinstance(level, int) else logging.WARNING


def build_logger(name: str) -> logging.Logger:
    """Create a Rich-backed logger controlled by `HARNESDK_LOG_LEVEL`."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    env_level = os.getenv("HARNESDK_LOG_LEVEL", "WARNING")
    handler = RichHandler(rich_tracebacks=True, show_time=False, show_path=False)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(_resolve_log_level(env_level))
    logger.propagate = False
    return logger
