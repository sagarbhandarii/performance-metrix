"""Centralized logging configuration for the performance-metrix project."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).with_name("logs")
RUN_TIMESTAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOGS_DIR / f"test_run_{RUN_TIMESTAMP}.log"

_INITIALIZED = False


def setup_logging(verbose: bool = False) -> Path:
    """Initialize root logging handlers for console + timestamped file."""
    global _INITIALIZED
    if _INITIALIZED:
        return LOG_FILE

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    _INITIALIZED = True
    root_logger.info("Logging initialized. Log file: %s", LOG_FILE)
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    """Return module logger by name."""
    return logging.getLogger(name)
