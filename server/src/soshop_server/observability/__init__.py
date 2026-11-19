"""SoShop-server observability helpers (request-ID, OpenTelemetry tracing).

Kept separate from ``soshop/observability/`` so the SoShop server can
evolve its own tracing stack without touching the legacy soshop
metrics/cost helpers that still live there.
"""

from soshop_server.observability.tracing import setup_tracing

__all__ = ["setup_tracing"]
