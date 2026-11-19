"""Voice selection and the failover policy of the keyless TTS.

The services are replaced by scripted fakes: each fake request either sends
audio after a delay or fails without any, which is how the real Edge endpoint
misbehaves for the native Vietnamese voices.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

pytest.importorskip("edge_tts")

from livekit.agents import APIConnectionError  # noqa: E402

from soshop.agent import edge_voice  # noqa: E402
from soshop.agent.edge_voice import (  # noqa: E402
    EdgeTTS,
    is_edge_voice,
    is_keyless_voice,
    split_for_google,
)

VI = "vi-VN-HoaiMyNeural"
VI_FALLBACK = edge_voice.FALLBACK_VOICES[VI]
EN = "en-US-AriaNeural"


class FakeEmitter:
    def __init__(self) -> None:
        self.mime: str | None = None
        self.data = b""
        self.flushed = False

    def initialize(self, *, mime_type: str, **_: object) -> None:
        assert self.mime is None, "initialized twice: a retry would replay audio"
        self.mime = mime_type

    def push(self, data: bytes) -> None:
        self.data += data

    def flush(self) -> None:
        self.flushed = True


class FakeService:
    """Scripted outcomes per request: ('ok', delay, payload), ('break', ...) or ('fail', delay)."""

    def __init__(self, script: list[tuple]) -> None:
        self.script = list(script)
        self.calls: list[str] = []

    def __call__(self, text: str, voice: str) -> AsyncIterator[bytes]:
        index = len(self.calls)
        self.calls.append(voice)
        step = self.script[index] if index < len(self.script) else ("fail", 0.0)

        async def _chunks() -> AsyncIterator[bytes]:
            await asyncio.sleep(step[1])
            if step[0] == "fail":
                raise RuntimeError("No audio was received")
            yield step[2]
            if step[0] == "break":
                raise RuntimeError("connection reset")
            yield b"-end"

        return _chunks()


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService([])
    monkeypatch.setattr(edge_voice, "_voice_audio", fake)
    monkeypatch.setattr(edge_voice, "_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(edge_voice, "_FLAKY_FIRST_AUDIO_S", 0.05)
    monkeypatch.setattr(edge_voice, "_FIRST_AUDIO_S", 0.3)
    return fake


async def _say(tts: EdgeTTS, text: str = "Chào cả nhà nha.") -> FakeEmitter:
    emitter = FakeEmitter()
    used = await edge_voice.deliver(text, tts.voice_plan(), emitter)  # type: ignore[arg-type]
    if used is not None:
        tts.report(used)
    return emitter


@pytest.mark.parametrize(
    ("voice_id", "edge", "keyless"),
    [
        ("en-US-AriaNeural", True, True),
        ("vi-VN-HoaiMyNeural", True, True),
        ("en-US-AvaMultilingualNeural", True, True),
        ("google-vi", False, True),
        ("google-", False, False),
        ("m8AHWg36LJTQWKmfeGVv", False, False),  # an ElevenLabs id
        ("", False, False),
        (None, False, False),
    ],
)
def test_voice_id_recognition(voice_id: str | None, edge: bool, keyless: bool) -> None:
    assert is_edge_voice(voice_id) is edge
    assert is_keyless_voice(voice_id) is keyless


def test_foreign_voice_id_falls_back_to_the_default_voice() -> None:
    assert EdgeTTS(default_voice=EN, voice_id="m8AHWg36LJTQWKmfeGVv").voice == EN
    assert EdgeTTS(default_voice=EN, voice_id="google-vi").voice == "google-vi"


def test_google_text_is_split_at_natural_pauses() -> None:
    text = (
        "Em váy này chất vải mềm, mặc lên mát và không nhăn. " * 3
        + "Dáng suông nên ai hơi tròn mặc vẫn rất tôn dáng, cả nhà yên tâm nha!"
    )
    pieces = split_for_google(text, limit=80)

    assert all(len(p) <= 80 for p in pieces)
    assert " ".join(pieces) == " ".join(text.split())
    assert all(p[-1] in ".,!" for p in pieces)  # every cut lands on punctuation here
    assert split_for_google("ngắn thôi") == ["ngắn thôi"]
    assert split_for_google("") == []


async def test_healthy_voice_speaks_on_the_first_request(service: FakeService) -> None:
    service.script = [("ok", 0.0, b"A")]

    emitter = await _say(EdgeTTS(default_voice="google-vi"))

    assert emitter.data == b"A-end" and emitter.mime == "audio/mp3" and emitter.flushed
    assert service.calls == ["google-vi"]


async def test_voice_without_a_fallback_is_retried(service: FakeService) -> None:
    service.script = [("fail", 0.0), ("ok", 0.0, b"E")]

    emitter = await _say(EdgeTTS(default_voice=EN))

    assert emitter.data == b"E-end"
    assert service.calls == [EN, EN]


async def test_flaky_voice_hands_a_failed_sentence_to_its_fallback(service: FakeService) -> None:
    service.script = [("fail", 0.0), ("ok", 0.0, b"F")]

    emitter = await _say(EdgeTTS(default_voice=VI))

    assert emitter.data == b"F-end"
    assert service.calls == [VI, VI_FALLBACK]


async def test_flaky_voice_that_is_merely_slow_is_not_waited_for(service: FakeService) -> None:
    service.script = [("ok", 0.2, b"SLOW"), ("ok", 0.0, b"F")]

    emitter = await _say(EdgeTTS(default_voice=VI))

    assert emitter.data == b"F-end"  # the stream was not held for the slow answer


async def test_repeated_failures_keep_the_fallback_in_charge_for_a_while(
    service: FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(edge_voice.time, "monotonic", lambda: now[0])
    tts = EdgeTTS(default_voice=VI)
    service.script = [("fail", 0.0), ("ok", 0.0, b"1"), ("fail", 0.0), ("ok", 0.0, b"2")]
    await _say(tts)
    await _say(tts)

    service.script += [("ok", 0.0, b"3")]
    await _say(tts)
    assert service.calls[-1] == VI_FALLBACK and len(service.calls) == 5  # no primary attempt

    # Once the breaker closes the primary gets one trial; a failure reopens it at once.
    now[0] += edge_voice._BREAKER_OPEN_S + 1
    service.script += [("fail", 0.0), ("ok", 0.0, b"4"), ("ok", 0.0, b"5")]
    await _say(tts)
    await _say(tts)
    assert service.calls[5:] == [VI, VI_FALLBACK, VI_FALLBACK]


async def test_a_primary_success_keeps_the_primary(service: FakeService) -> None:
    tts = EdgeTTS(default_voice=VI)
    service.script = [("fail", 0.0), ("ok", 0.0, b"1"), ("ok", 0.0, b"2"), ("ok", 0.0, b"3")]

    await _say(tts)
    await _say(tts)
    await _say(tts)

    assert service.calls == [VI, VI_FALLBACK, VI, VI]


async def test_total_outage_raises_instead_of_dropping_the_sentence(service: FakeService) -> None:
    with pytest.raises(APIConnectionError):
        await _say(EdgeTTS(default_voice=VI))
    assert service.calls == [VI, VI_FALLBACK, VI_FALLBACK]


async def test_stream_breaking_mid_sentence_is_not_replayed(service: FakeService) -> None:
    service.script = [("break", 0.0, b"half"), ("ok", 0.0, b"again")]

    with pytest.raises(APIConnectionError):
        await _say(EdgeTTS(default_voice=VI))
    assert service.calls == [VI]  # no second attempt after audio already went out


@pytest.mark.parametrize("text", ["...", "-", "mm"])
async def test_unspeakable_fragment_becomes_silence(service: FakeService, text: str) -> None:
    emitter = await _say(EdgeTTS(default_voice="google-vi"), text)

    assert emitter.mime == "audio/pcm" and emitter.flushed
    assert emitter.data and set(emitter.data) == {0}
