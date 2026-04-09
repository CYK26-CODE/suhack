"""Structured JSON logging with structlog.

Every log event carries run_id, module, and stage fields automatically via contextvars.
"""

import logging
import sys

import structlog


def redact_secrets(
    logger: structlog.types.WrappedLogger,
    method: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Filter sensitive keys from all log output."""
    sensitive_keys = ("api_key", "token", "secret", "password", "grok_api_key", "github_token")
    for key in sensitive_keys:
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog for structured JSON output."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
