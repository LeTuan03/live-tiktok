"""Structured logging setup using loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_dir: Path, log_level: str = "INFO", debug: bool = False) -> None:
    """Configure application logging with file rotation and console output.

    Args:
        log_dir: Directory to store log files.
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        debug: If True, forces DEBUG level and adds more verbose console output.
    """
    # Remove default handler
    logger.remove()

    effective_level = "DEBUG" if debug else log_level

    # Console handler — colorized, concise
    console_format = (
        "<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(
        sys.stderr,
        format=console_format,
        level=effective_level,
        colorize=True,
        backtrace=debug,
        diagnose=debug,
    )

    # File handler — structured, rotated
    log_dir.mkdir(parents=True, exist_ok=True)
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )
    logger.add(
        log_dir / "liveflow_{time:YYYY-MM-DD}.log",
        format=file_format,
        level=effective_level,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    # Error-only file for quick troubleshooting
    logger.add(
        log_dir / "errors_{time:YYYY-MM-DD}.log",
        format=file_format,
        level="ERROR",
        rotation="5 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    logger.info(
        "Logging initialized | level={} | dir={}",
        effective_level,
        log_dir,
    )


def get_logger(name: str = "liveflow"):
    """Get a contextualized logger instance.

    Args:
        name: Module or component name for log context.

    Returns:
        A loguru logger bound with the given name context.
    """
    return logger.bind(name=name)
