"""structlog configuration for the agent service.

Every line carries `partner_id, merchant_id, agent_name, trace_id, span_id`
when those values are bound. Cloud Logging recognises structlog JSON output
without further config.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import structlog
from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _add_trace_context(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    return event_dict


def init_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(agent_name: str) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger().bind(agent_name=agent_name))
