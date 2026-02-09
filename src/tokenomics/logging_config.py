"""Structured logging configuration with multiple output streams."""

import logging
import logging.handlers
from pathlib import Path

import structlog

from tokenomics.config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Set up structured logging with console + file outputs."""
    # Ensure log directories exist
    for log_path in [config.app_log, config.trade_log, config.decision_log]:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # Shared structlog processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # JSON formatter for file output
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    # Console formatter for human-readable output
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=shared_processors,
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level.upper()))

    # Silence noisy third-party loggers that may leak secrets in URLs
    for noisy_logger in ["urllib3", "httpcore", "httpx", "google_genai"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # App log file handler
    app_handler = logging.handlers.RotatingFileHandler(
        config.app_log,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
    )
    app_handler.setFormatter(json_formatter)
    root_logger.addHandler(app_handler)

    # Trade log handler (separate logger)
    trade_logger = logging.getLogger("tokenomics.trades")
    trade_handler = logging.handlers.RotatingFileHandler(
        config.trade_log,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
    )
    trade_handler.setFormatter(json_formatter)
    trade_logger.addHandler(trade_handler)
    trade_logger.propagate = True

    # Decision log handler (separate logger)
    decision_logger = logging.getLogger("tokenomics.decisions")
    decision_handler = logging.handlers.RotatingFileHandler(
        config.decision_log,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
    )
    decision_handler.setFormatter(json_formatter)
    decision_logger.addHandler(decision_handler)
    decision_logger.propagate = True


def get_trade_logger() -> structlog.stdlib.BoundLogger:
    """Get the trade-specific logger."""
    return structlog.get_logger("tokenomics.trades")


def get_decision_logger() -> structlog.stdlib.BoundLogger:
    """Get the decision-specific logger."""
    return structlog.get_logger("tokenomics.decisions")
