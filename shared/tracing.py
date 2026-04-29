"""OpenTelemetry tracing wired to Cloud Trace.

Span names and attribute conventions are pinned in the implementation plan
§8 so log/trace dashboards remain queryable across the five agents.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

_INITIALISED = False


def init_tracing(service_name: str) -> None:
    """Idempotently install the global OTel tracer + Cloud Trace exporter."""
    global _INITIALISED
    if _INITIALISED:
        return

    try:
        # Lazy import: dev installs without GCP creds shouldn't crash on `from shared.tracing import ...`
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    except ImportError:
        CloudTraceSpanExporter = None  # type: ignore[assignment]

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if CloudTraceSpanExporter is not None:
        try:
            provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
        except Exception:
            # No ADC, no project, or Cloud Trace API disabled — fall back to noop.
            pass

    trace.set_tracer_provider(provider)
    _INITIALISED = True


def get_tracer(name: str = "surplusas.agents") -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def a2a_client_span(audience: str, headers: dict[str, str]) -> Iterator[Span]:
    """Open a span around an outbound A2A POST and inject `traceparent` into `headers`."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "a2a.call_peer",
        attributes={"peer.url": audience},
    ) as span:
        propagate.inject(headers)
        yield span


def set_attrs(span: Span, **attrs: Any) -> None:
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)
