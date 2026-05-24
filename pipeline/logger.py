"""
pipeline/logger.py

Centralised logging configuration for the entire pipeline.

Usage
-----
    from pipeline.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Starting pipeline step")

Design decisions
----------------
- One StreamHandler to stdout so logs appear in the uvicorn console.
- A second optional FileHandler writes a persistent log to pipeline.log
  (disabled by default — set PIPELINE_LOG_FILE env var to enable).
- Format includes timestamp, level, module name, and message so log lines
  are self-contained when tailing multiple services.
- get_logger() is idempotent: calling it multiple times for the same name
  never adds duplicate handlers.
"""

from __future__ import annotations

import logging
import os
import sys


# Log format: timestamp | LEVEL    | module.name | message
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Default level — change to logging.WARNING in production
_DEFAULT_LEVEL = logging.DEBUG


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger with consistent formatting.

    Parameters
    ----------
    name : Typically __name__ of the calling module.
           Example: "pipeline.runner", "ingestion_parsing.main"

    Returns
    -------
    logging.Logger configured with stdout StreamHandler.
    If PIPELINE_LOG_FILE env var is set, a FileHandler is also attached.
    """
    logger = logging.getLogger(name)

    # Guard against duplicate handlers if get_logger() is called more than once
    if logger.handlers:
        return logger

    logger.setLevel(_DEFAULT_LEVEL)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── Console handler (always on) ─────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(_DEFAULT_LEVEL)
    logger.addHandler(console_handler)

    # ── Optional file handler ────────────────────────────────────────────────
    log_file = os.getenv("PIPELINE_LOG_FILE", "")
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.INFO)   # Only INFO+ to file
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning(f"Could not open log file '{log_file}': {exc}")

    # Prevent log records from propagating to the root logger (avoids duplicate output)
    logger.propagate = False

    return logger
