"""
Structured Logging Configuration

Provides unified logging setup with JSON output for log aggregation,
structured logging with context, and easy debugging.

Usage:
    from utils.logging_config import setup_logging, get_logger

    logger = get_logger(__name__)
    logger.info("Processing video", video_url=url, format=format)
"""

import os
import sys
import logging
import json
from datetime import datetime
from typing import Any, Dict, Optional
from functools import lru_cache


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Outputs log messages as JSON for easy parsing by log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base log data
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields (context)
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        # Add any custom fields from record.__dict__
        for key, value in record.__dict__.items():
            if key not in (
                "msg",
                "args",
                "exc_info",
                "exc_text",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "funcName",
                "lineno",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "name",
                "stack_info",
            ):
                if not key.startswith("_"):
                    log_data[key] = value

        return json.dumps(log_data, default=str)


class ColoredFormatter(logging.Formatter):
    """
    Colored formatter for development.
    Adds colors to console output for easier reading.
    """

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


class ContextFilter(logging.Filter):
    """
    Adds context to all log records.
    Use add_context() to add fields to all subsequent logs.
    """

    _context: Dict[str, Any] = {}

    @classmethod
    def add_context(cls, **kwargs) -> None:
        """Add fields to be included in all log records"""
        cls._context.update(kwargs)

    @classmethod
    def clear_context(cls) -> None:
        """Clear all context"""
        cls._context.clear()

    @classmethod
    def remove_context(cls, *keys: str) -> None:
        """Remove specific context fields"""
        for key in keys:
            cls._context.pop(key, None)

    def filter(self, record: logging.LogRecord) -> bool:
        # Add context to record
        record.extra = self._context.copy()
        return True


class LoggerWrapper:
    """
    Wraps a standard logger to provide structured logging support.
    This allows calling logger.info("msg", key=value) syntax.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, exc_info=None, stack_info=None, **kwargs):
        if kwargs:
            record = self._logger.makeRecord(
                self._logger.name,
                level,
                "",
                0,
                msg,
                (),
                exc_info,
                stack_info=stack_info,
            )
            record.extra = kwargs
            self._logger.handle(record)
        else:
            self._logger.log(level, msg, exc_info=exc_info, stack_info=stack_info)

    def debug(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.DEBUG, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def info(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(logging.INFO, msg, exc_info=exc_info, stack_info=stack_info, **kwargs)

    def warning(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.WARNING, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def error(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.ERROR, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def critical(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.CRITICAL, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )


@lru_cache(maxsize=1)
def get_logger(name: str) -> LoggerWrapper:
    """
    Get a logger wrapper that supports structured logging.
    Results are cached for performance.

    Returns a LoggerWrapper that supports calling logger.info("msg", key=value) syntax.

    Args:
        name: Logger name, typically __name__ from the calling module

    Returns:
        LoggerWrapper instance with structured logging support
    """
    return LoggerWrapper(logging.getLogger(name))


def setup_logging(
    level: str = None, json_format: bool = None, include_context: bool = True
) -> None:
    """
    Set up logging configuration for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               Defaults to INFO or DEBUG if DEBUG env var is set
        json_format: Use JSON output for log aggregation
                    Defaults to True in production (not interactive)
        include_context: Include context filter for structured logging
    """
    # Determine settings
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    if json_format is None:
        # Use JSON in production (non-interactive)
        json_format = not sys.stdout.isatty()

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))

    # Set formatter based on environment
    if json_format:
        formatter = JSONFormatter()
    else:
        # Colored output for development
        formatter = ColoredFormatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Add context filter if requested
    if include_context:
        context_filter = ContextFilter()
        root_logger.addFilter(context_filter)

    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured: level={level}, json_format={json_format}, include_context={include_context}"
    )


class StructuredLogger:
    """
    A wrapper around Logger that supports structured logging with extra fields.

    Usage:
        logger = StructuredLogger(__name__)
        logger.info("Processing video", video_id="abc123", user_id=456)
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, msg: str, **kwargs):
        """Log with extra fields"""
        self._logger.log(level, msg, extra=kwargs)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)


class LoggerWrapper:
    """
    Wraps a standard logger to provide structured logging support.
    This allows calling logger.info("msg", key=value) syntax.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, exc_info=None, stack_info=None, **kwargs):
        if kwargs:
            record = self._logger.makeRecord(
                self._logger.name,
                level,
                "",
                0,
                msg,
                (),
                exc_info,
                stack_info=stack_info,
            )
            record.extra = kwargs
            self._logger.handle(record)
        else:
            self._logger.log(level, msg, exc_info=exc_info, stack_info=stack_info)

    def debug(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.DEBUG, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def info(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(logging.INFO, msg, exc_info=exc_info, stack_info=stack_info, **kwargs)

    def warning(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.WARNING, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def error(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.ERROR, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )

    def critical(self, msg: str, exc_info=None, stack_info=None, **kwargs):
        self._log(
            logging.CRITICAL, msg, exc_info=exc_info, stack_info=stack_info, **kwargs
        )


def log_context(**kwargs) -> None:
    """
    Add context to all subsequent log messages.

    Usage:
        log_context(video_id="abc123", user_id=456)
        logger.info("Processing video")  # Will include video_id and user_id
    """
    ContextFilter.add_context(**kwargs)


def clear_context() -> None:
    """Clear all context from log messages"""
    ContextFilter.clear_context()
