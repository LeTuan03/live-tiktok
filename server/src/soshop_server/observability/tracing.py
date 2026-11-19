"""OpenTelemetry FastAPI auto-instrumentation, gated by config.

Activation is two-tier:

* ``OTEL_ENABLED`` toggles the whole feature. When off ``setup_tracing`` is
  never invoked (see :func:`soshop_server.api.app.create_app`).
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` toggles the exporter specifically. When
  unset we still install the FastAPI instrumentor so spans are *recorded*
  (and visible via in-process processors a test harness might attach) —
  this matches the OpenTelemetry-Python documented pattern.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from soshop_server.core.config import settings


def setup_tracing(app: FastAPI) -> None:
    """Install a ``TracerProvider`` and auto-instrument the FastAPI app.

    Safe to call exactly once per app instance. The provider is only set on
    the global ``trace`` API when it has not already been initialised so
    repeat calls in tests don't trip the SDK's "TracerProvider already
    set" guard.
    """
    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    # Only set the global provider if one wasn't already installed — keeps
    # tests that build multiple ``create_app`` instances quiet.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
