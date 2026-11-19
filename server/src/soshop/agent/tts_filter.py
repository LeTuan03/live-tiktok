"""TTS-stream filter that strips leaked tool-call markup before speech.

Groq's Llama 3.3 70B occasionally emits the pitch tools' function-call
as plain text in the LLM stream instead of through the structured
function-calling channel — e.g. ``function signal_pitch_end``, or
``<function=signal_pitch_end>``. Those tokens flow into ``tts_node``
and ElevenLabs reads them aloud (the underscore becomes a natural
pause, so the listener hears "function signal pitch end"). The
function-calling mechanism still fires alongside the text on most
turns, so the pitch popup still dismisses; the problem is purely an
audible leak.

The soshop-fork repo has a similar filter (`_filter_tool_call_text`)
but it only catches JSON-shaped emissions. The SoShop tools are
zero-argument, so the leak surfaces as a bare identifier rather than
JSON — this filter is tuned for that shape.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable

__all__ = ["TOOL_CALL_LEAK_RE", "filter_tool_call_text"]

# Patterns we strip from the TTS stream. Each match is replaced with
# empty text. The regex is case-insensitive and ignores leading
# punctuation so a sentence-final "...signal_pitch_end." or a "[function:
# signal_pitch_end]" wrapper both get stripped cleanly.
TOOL_CALL_LEAK_RE = re.compile(
    r"""(?ix)
    (?:
        # XML-ish wrapper: <function=foo>...</function> or <function foo>
        <\s*/?\s*function\b[^>]*>
        |
        # Bracketed: [function: signal_pitch_end] / {function signal_product_pitch}
        [\[\{]\s*function[\s:=]+signal_(?:pitch_end|product_pitch)\s*[\]\}]
        |
        # Prose: "function signal_pitch_end" / "function: signal_product_pitch"
        \bfunction[\s:=]+signal_(?:pitch_end|product_pitch)\b
        |
        # Bare tool name leaks: "signal_pitch_end" / "signal_product_pitch"
        \bsignal_(?:pitch_end|product_pitch)\b
    )
    """,
)

# Longest possible match plus a small safety margin. Streamed chunks are
# usually a token or two, so a partial pattern can straddle a chunk
# boundary; we hold this much trailing text back from the consumer until
# the next chunk arrives so the regex sees a complete pattern.
_HOLD_BACK_CHARS = 64


async def filter_tool_call_text(
    text_stream: AsyncIterable[str],
) -> AsyncIterable[str]:
    """Pass ``text_stream`` through, removing any tool-call-leak substring.

    Streaming-friendly: emits chunks as they arrive once enough trailing
    text has accumulated that the regex can't be cut mid-pattern. The
    final residue is flushed when the upstream stream ends.
    """
    buffer = ""
    async for chunk in text_stream:
        buffer += chunk
        buffer = TOOL_CALL_LEAK_RE.sub("", buffer)
        if len(buffer) > _HOLD_BACK_CHARS:
            emit_to = len(buffer) - _HOLD_BACK_CHARS
            yield buffer[:emit_to]
            buffer = buffer[emit_to:]
    if buffer:
        buffer = TOOL_CALL_LEAK_RE.sub("", buffer)
        if buffer:
            yield buffer
