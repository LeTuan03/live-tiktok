"""Prometheus metrics for soshop-server runtime health.

Surfaces the four metrics the ops dashboard cares about:

* `soshop_active_rooms` — gauge: concurrent LiveKit rooms.
* `soshop_active_sessions` — gauge: in-flight feed sessions tracked by the
  registry.
* `soshop_persona_swap_seconds` — histogram: end-to-end persona swap latency,
  labelled by `swap_mode` (`single_room` vs. `ping_pong`) for the dashboard's
  separate-series requirement.
* `soshop_lemonslice_warmup_seconds` — histogram: LemonSlice avatar warmup
  latency.
* `soshop_dispatch_total` / `soshop_dispatch_errors_total` — counters: LiveKit
  agent-dispatch attempts and failures, labelled by `reason`. The dashboard
  computes the dispatch error *rate* as `errors / total`.
* `soshop_agent_provider_errors_total` — counter: agent-side LLM/STT/TTS
  errors, labelled by `provider` (`groq`, `elevenlabs`, …) and `kind`
  (`llm`, `stt`, `tts`).

The module also exposes `metrics_router`, a FastAPI router that publishes
the standard `/metrics` text exposition for Prometheus scraping. Mount it in
the app factory:

    from soshop.observability import metrics_router
    app.include_router(metrics_router)

All instruments are registered against the default global
`prometheus_client.REGISTRY`, which is what `generate_latest()` reads.
Tests that want a clean registry can call `prometheus_client.REGISTRY` apis
directly; the metric names are stable and match the dashboard JSON in
`server/ops/grafana/runtime.json`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
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

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


# Buckets chosen against the swap budget: p95 target is ≤4s, hard
# alert threshold is 6s. Buckets straddle both so the histogram is dense
# where the SLO lives.
_SWAP_BUCKETS = (0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0)

# LemonSlice warmup typically completes in 1–3s; keep buckets coarser past 5s.
_WARMUP_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 30.0)


active_rooms: Gauge = Gauge(
    "soshop_active_rooms",
    "Current number of LiveKit rooms with at least one active companion session.",
)

active_sessions: Gauge = Gauge(
    "soshop_active_sessions",
    "Current number of in-flight feed sessions tracked by the registry.",
)

persona_swap_seconds: Histogram = Histogram(
    "soshop_persona_swap_seconds",
    "End-to-end persona swap latency (delete-dispatch → new-dispatch acked).",
    labelnames=("swap_mode",),
    buckets=_SWAP_BUCKETS,
)

lemonslice_warmup_seconds: Histogram = Histogram(
    "soshop_lemonslice_warmup_seconds",
    "LemonSlice avatar warmup latency (request → avatar ready).",
    buckets=_WARMUP_BUCKETS,
)

dispatch_total: Counter = Counter(
    "soshop_dispatch_total",
    "Total LiveKit agent-dispatch attempts.",
    labelnames=("agent_name",),
)

dispatch_errors_total: Counter = Counter(
    "soshop_dispatch_errors_total",
    "LiveKit agent-dispatch failures.",
    labelnames=("agent_name", "reason"),
)

agent_provider_errors_total: Counter = Counter(
    "soshop_agent_provider_errors_total",
    "Agent-side provider errors (LLM/STT/TTS).",
    labelnames=("provider", "kind"),
)


def set_active_rooms(value: int) -> None:
    active_rooms.set(value)


def set_active_sessions(value: int) -> None:
    active_sessions.set(value)


def record_dispatch_attempt(agent_name: str) -> None:
    dispatch_total.labels(agent_name=agent_name).inc()


def record_dispatch_error(agent_name: str, reason: str) -> None:
    dispatch_errors_total.labels(agent_name=agent_name, reason=reason).inc()


def record_agent_provider_error(provider: str, kind: str) -> None:
    agent_provider_errors_total.labels(provider=provider, kind=kind).inc()


@contextmanager
def observe_persona_swap(swap_mode: str) -> Iterator[None]:
    """Time a persona swap and record into `soshop_persona_swap_seconds`.

    `swap_mode` should be `"single_room"` or `"ping_pong"` to match the
    dashboard's series split.
    """
    started = monotonic()
    try:
        yield
    finally:
        persona_swap_seconds.labels(swap_mode=swap_mode).observe(monotonic() - started)


@contextmanager
def observe_lemonslice_warmup() -> Iterator[None]:
    """Time a LemonSlice avatar warmup call."""
    started = monotonic()
    try:
        yield
    finally:
        lemonslice_warmup_seconds.observe(monotonic() - started)


metrics_router = APIRouter()


@metrics_router.get(
    "/metrics",
    summary="Prometheus metrics scrape endpoint",
    response_class=Response,
)
def prometheus_metrics() -> Response:
    """Standard Prometheus text exposition for scraping."""
    return Response(content=generate_latest(REGISTRY), media_type=METRICS_CONTENT_TYPE)
