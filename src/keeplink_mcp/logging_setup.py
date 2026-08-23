"""Structured logging configuration for the application."""

import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB default
    backup_count: int = 5,
) -> logging.Logger:
    """Configure and return the application root logger.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path for persistent log output.
        max_bytes: Maximum size of each log file before rotation (default: 10 MB).
        backup_count: Number of backup log files to keep (default: 5).

    Returns:
        Configured root logger instance.
    """
    logger = logging.getLogger("keeplink")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — always enabled
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler — only if path provided
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
