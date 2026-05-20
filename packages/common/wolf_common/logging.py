"""Structured JSON logging via structlog.

Call `configure_logging(level, environment)` once at service startup.
After that, use `structlog.get_logger()` anywhere — it binds the current
trace/span IDs automatically via the OTel processor.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor


def _add_otel_context(
    logger: Any,  # noqa: ANN401
    method: str,
    event_dict: EventDict,
) -> EventDict:
    """Inject OpenTelemetry trace/span IDs when a span is active."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            ctx = span.get_span_context()
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001, S110
        pass
    return event_dict


def configure_logging(level: str = "INFO", environment: str = "production") -> None:
    """Configure structlog for the calling service.

    In development, output is colorized and human-readable.
    In all other environments, output is structured JSON (log-aggregator-friendly).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_otel_context,
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "development":
        renderer: Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
