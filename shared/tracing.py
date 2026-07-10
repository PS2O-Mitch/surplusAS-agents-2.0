"""OpenTelemetry tracing, exported over OTLP when configured.

Spans are always recorded; they leave the process only if
`OTEL_EXPORTER_OTLP_ENDPOINT` is set (any OTLP-speaking backend — Grafana,
Honeycomb, a local collector). Without it the provider is a no-op.

Span names and attribute conventions are pinned in the implementation plan
§8 so log/trace dashboards remain queryable across the five agents.
"""

from __future__ import annotations

import contextlib
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry.trace import Span

_INITIALISED = False


def init_tracing(service_name: str) -> None:
    """Idempotently install the global OTel tracer (+ OTLP exporter if configured)."""
    global _INITIALISED
    if _INITIALISED:
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Export only when an OTLP endpoint is configured; a broken exporter
    # must not crash the process — spans just stay in-process (no-op).
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        with contextlib.suppress(Exception):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

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
