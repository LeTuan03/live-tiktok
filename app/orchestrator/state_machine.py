"""State Machine definitions for Live Session Orchestrator (§3, §5)."""

from __future__ import annotations

from enum import Enum


class LiveSessionState(str, Enum):
    """Discrete states of an autonomous livestream session."""

    IDLE = "idle"
    PREFLIGHT = "preflight"
    STARTING = "starting"
    INTRO = "intro"
    PITCHING_SKU = "pitching_sku"
    HANDLING_COMMENT = "handling_comment"
    CALL_TO_ACTION = "call_to_action"
    NEXT_SKU = "next_sku"
    PAUSED = "paused"
    ERROR_FALLBACK = "error_fallback"
    STOPPING = "stopping"
    STOPPED = "stopped"
