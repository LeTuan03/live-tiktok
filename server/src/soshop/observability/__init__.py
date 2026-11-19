"""Runtime observability primitives for soshop-server."""

from soshop.observability.metrics import (
    METRICS_CONTENT_TYPE,
    active_rooms,
    active_sessions,
    agent_provider_errors_total,
    dispatch_errors_total,
    dispatch_total,
    lemonslice_warmup_seconds,
    metrics_router,
    observe_lemonslice_warmup,
    observe_persona_swap,
    persona_swap_seconds,
    record_agent_provider_error,
    record_dispatch_attempt,
    record_dispatch_error,
    set_active_rooms,
    set_active_sessions,
)

__all__ = [
    "METRICS_CONTENT_TYPE",
    "active_rooms",
    "active_sessions",
    "agent_provider_errors_total",
    "dispatch_errors_total",
    "dispatch_total",
    "lemonslice_warmup_seconds",
    "metrics_router",
    "observe_lemonslice_warmup",
    "observe_persona_swap",
    "persona_swap_seconds",
    "record_agent_provider_error",
    "record_dispatch_attempt",
    "record_dispatch_error",
    "set_active_rooms",
    "set_active_sessions",
]
