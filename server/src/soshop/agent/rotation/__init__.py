"""Channel rotation pacing for the SoShop agent.

The 24/7 channel cycles personas off the LLM's actual speech completion
(``session.output.audio.playback_finished``) rather than a wall-clock
timer, so swaps land organically when each pitch finishes instead of
cutting off mid-sentence or leaving dead air. This package bundles the
three concerns that pacing decomposes into:

- :class:`PitchCompletionSignal` — "is the current pitch finished?"
- :func:`jitter_pause`          — "how long to pause between pitches?"
- :class:`PitchOrchestrator`    — "which pitch is next, log it, drive it"

The actual persona swap mechanics still live in
:mod:`soshop.agent.swap` — only the *trigger* for the swap moved.
"""

from soshop.agent.rotation.completion import (
    CompletionOutcome,
    PitchCompletionSignal,
    SpeechHandleCompletion,
    build_default_completion,
)
from soshop.agent.rotation.cursor import RotationCursor
from soshop.agent.rotation.jitter import default_jitter, jitter_pause
from soshop.agent.rotation.orchestrator import (
    CHANNEL_LOOP_SWAP_SOURCE,
    ChannelLoopDeps,
    PitchOrchestrator,
    _ad_to_dispatch_metadata,
)

__all__ = [
    "CHANNEL_LOOP_SWAP_SOURCE",
    "ChannelLoopDeps",
    "CompletionOutcome",
    "PitchCompletionSignal",
    "PitchOrchestrator",
    "RotationCursor",
    "SpeechHandleCompletion",
    "_ad_to_dispatch_metadata",
    "build_default_completion",
    "default_jitter",
    "jitter_pause",
]
