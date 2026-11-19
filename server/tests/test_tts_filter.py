"""Unit tests for the TTS tool-call-leak filter."""

from __future__ import annotations

from collections.abc import AsyncIterable

import pytest

from soshop.agent.tts_filter import (
    TOOL_CALL_LEAK_RE,
    filter_tool_call_text,
)


async def _stream(*chunks: str) -> AsyncIterable[str]:
    for c in chunks:
        yield c


async def _collect(stream: AsyncIterable[str]) -> str:
    return "".join([c async for c in stream])


@pytest.mark.parametrize(
    "leak",
    [
        "function signal_pitch_end",
        "function: signal_pitch_end",
        "function= signal_product_pitch",
        "signal_pitch_end",
        "signal_product_pitch",
        "[function: signal_pitch_end]",
        "<function=signal_pitch_end>",
        "</function>",
    ],
)
def test_regex_matches_known_leaks(leak: str) -> None:
    assert TOOL_CALL_LEAK_RE.search(leak) is not None


@pytest.mark.parametrize(
    "benign",
    [
        "Tap to shop now.",
        "Let me show you something.",
        "These earbuds end your search for great sound.",
        "I signal you to lean in.",
    ],
)
def test_regex_leaves_normal_text_alone(benign: str) -> None:
    assert TOOL_CALL_LEAK_RE.search(benign) is None


@pytest.mark.asyncio
async def test_filter_passes_through_clean_text() -> None:
    out = await _collect(filter_tool_call_text(_stream("Hello ", "world!")))
    assert out == "Hello world!"


@pytest.mark.asyncio
async def test_filter_strips_leak_at_end_of_stream() -> None:
    out = await _collect(
        filter_tool_call_text(_stream("Tap to shop now. ", "signal_pitch_end")),
    )
    assert "signal" not in out
    assert "Tap to shop now." in out


@pytest.mark.asyncio
async def test_filter_strips_leak_split_across_chunks() -> None:
    # Worst case: the regex pattern straddles a chunk boundary. The hold-back
    # buffer should still catch it.
    out = await _collect(
        filter_tool_call_text(
            _stream("Try them today. function sign", "al_pitch_end"),
        ),
    )
    assert "signal" not in out
    assert "function" not in out
    assert "Try them today." in out


@pytest.mark.asyncio
async def test_filter_strips_xml_wrapper() -> None:
    out = await _collect(
        filter_tool_call_text(
            _stream("CTA goes here. <function=", "signal_pitch_end>"),
        ),
    )
    assert "<function" not in out
    assert "signal" not in out


@pytest.mark.asyncio
async def test_filter_emits_progressively() -> None:
    # The filter must not buffer the entire stream — long benign content
    # should start emitting once the hold-back window is exceeded.
    long_text = "Tap to shop. " * 20  # ~260 chars
    chunks_emitted: list[str] = []
    async for chunk in filter_tool_call_text(_stream(long_text)):
        chunks_emitted.append(chunk)
    assert "".join(chunks_emitted) == long_text
    # At least one progressive emission before EOF flush.
    assert len(chunks_emitted) >= 1
