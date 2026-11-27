"""The relay's pacing, splicing and failure handling, with stand-in processes.

The encoders and pushers here are small Python processes, not ffmpeg: an
"encoder" writes a prepared MPEG-TS byte stream whose packets carry PCRs, a
"pusher" appends whatever it reads to a file. That exercises the real pipes,
threads and timing without ffmpeg or a network.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from soshop.broadcast.timeline import Position
from soshop.restream.encoder import Profile
from soshop.restream.plan import Cue, Source
from soshop.restream.relay import (
    LEAD_S,
    PCR_HZ,
    TS_PACKET,
    Output,
    Relay,
    _lines,
    packet_pcr,
)

_ENCODE = "import sys; sys.stdout.buffer.write(open(sys.argv[1], 'rb').read())"
_FAIL = "import sys; sys.stderr.write('boom: no such file\\n'); sys.exit(1)"
_PUSH = (  # copies stdin to a file and reports progress the way ffmpeg -progress does
    "import sys\n"
    "total = 0\n"
    "with open(sys.argv[1], 'ab') as out:\n"
    "    while chunk := sys.stdin.buffer.read1(65536):\n"
    "        out.write(chunk)\n"
    "        total += len(chunk)\n"
    "        print(f'total_size={total}\\rprogress=continue', flush=True)\n"
)
_REFUSE = (
    "import sys\n"
    "sys.stderr.write('[rtmp] ' + sys.argv[2] + ': Input/output error\\n')\n"
    "sys.exit(1)\n"
)


def ts_stream(seconds: float, *, first_pcr_s: float, step_s: float = 0.1) -> bytes:
    """A fake MPEG-TS stream: a PCR packet every ``step_s``, three data packets after each."""
    out = bytearray()
    for i in range(round(seconds / step_s) + 1):
        base, ext = divmod(round((first_pcr_s + i * step_s) * PCR_HZ), 300)
        pcr = bytearray(TS_PACKET)
        pcr[0:6] = bytes([0x47, 0x01, 0x00, 0x20 | (i & 0x0F), 183, 0x10])
        pcr[6:12] = bytes(
            [
                (base >> 25) & 0xFF,
                (base >> 17) & 0xFF,
                (base >> 9) & 0xFF,
                (base >> 1) & 0xFF,
                ((base & 1) << 7) | 0x7E | ((ext >> 8) & 1),
                ext & 0xFF,
            ]
        )
        out += pcr
        for j in range(3):
            data = bytearray([0x47, 0x01, 0x00, 0x10 | (j & 0x0F)]) + bytes([i % 256]) * 184
            out += data
    return bytes(out)


class Procs:
    """Spawns stand-ins: encoders from a list of outputs, pushers writing to a file."""

    def __init__(self, tmp: Path, outputs: list[bytes | None], *, refuse: bool = False) -> None:
        self.tmp = tmp
        self.outputs = list(outputs)
        self.refuse = refuse
        self.pushed = tmp / "pushed.bin"

    def __call__(self, argv: list[str], stdin: bool) -> subprocess.Popen[bytes]:
        if stdin:
            script = _REFUSE if self.refuse else _PUSH
            return subprocess.Popen(  # noqa: S603 - test stand-in
                [sys.executable, "-c", script, str(self.pushed), argv[-1]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        data = self.outputs.pop(0)
        if data is None:
            cmd = [sys.executable, "-c", _FAIL]
        else:
            path = self.tmp / f"segment-{len(self.outputs)}.ts"
            path.write_bytes(data)
            cmd = [sys.executable, "-c", _ENCODE, str(path)]
        return subprocess.Popen(  # noqa: S603 - test stand-in
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

    def received(self) -> bytes:
        return self.pushed.read_bytes() if self.pushed.exists() else b""


def cue(item: str, length_ms: int) -> Cue:
    return Cue("play", Position("body", item, 0, 0, 0, length_ms), Source(f"{item}.mp4", 60_000))


def wait_until(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def relay_for(procs: Procs, tmp: Path) -> Relay:
    relay = Relay("ffmpeg", Profile(), tmp, spawn=procs)
    relay.set_outputs({"dest": Output("rtmp://h/app/stream-key-0001", ("stream-key-0001",))})
    relay.tick()  # starts the pusher
    return relay


def test_packet_pcr_reads_the_clock_and_skips_packets_without_one() -> None:
    data = ts_stream(0.2, first_pcr_s=3.5)
    assert packet_pcr(data, 0) == round(3.5 * PCR_HZ)
    assert packet_pcr(data, TS_PACKET) is None
    assert packet_pcr(data, 4 * TS_PACKET) == round(3.6 * PCR_HZ)
    assert packet_pcr(b"\x00" * TS_PACKET, 0) is None


def test_a_queued_segment_follows_without_a_gap_at_the_stream_pace(tmp_path: Path) -> None:
    first, second = ts_stream(1.0, first_pcr_s=5.0), ts_stream(1.0, first_pcr_s=90.0)
    procs = Procs(tmp_path, [first, second])
    relay = relay_for(procs, tmp_path)
    began = time.monotonic()
    relay.start(cue("a", 1_000), "a.mp4", True)
    relay.queue(cue("b", 1_000), "b.mp4", True)
    feed = relay.feed()
    assert feed is not None and feed.queued is not None and feed.queued.position.item_id == "b"
    wait_until(lambda: relay.feed() is None)
    took = time.monotonic() - began
    relay.tick()
    live = relay.statuses()["dest"].state  # the stand-in reported bytes written
    relay.close()
    assert live == "live"
    assert procs.received() == first + second  # whole, in order, nothing interleaved
    assert 2.0 - LEAD_S - 0.25 <= took <= 4.0  # paced by PCR: two seconds of media


def test_a_restart_cuts_the_old_segment_cleanly(tmp_path: Path) -> None:
    long, short = ts_stream(5.0, first_pcr_s=1.0), ts_stream(0.5, first_pcr_s=40.0)
    procs = Procs(tmp_path, [long, short])
    relay = relay_for(procs, tmp_path)
    relay.start(cue("a", 5_000), "a.mp4", True)
    wait_until(lambda: (feed := relay.feed()) is not None and feed.offset_ms >= 400)
    relay.start(cue("b", 500), "b.mp4", True)
    wait_until(lambda: relay.feed() is None)
    relay.close()
    received = procs.received()
    assert received.endswith(short)
    cut = received[: len(received) - len(short)]
    assert 0 < len(cut) < len(long) and long.startswith(cut)
    assert len(cut) % TS_PACKET == 0


def test_a_failed_encoder_is_reported_for_its_slot(tmp_path: Path) -> None:
    procs = Procs(tmp_path, [None])
    relay = relay_for(procs, tmp_path)
    relay.start(cue("a", 5_000), "a.mp4", True)
    wait_until(lambda: relay.failure() is not None)
    failure = relay.failure()
    assert failure is not None and failure.slot == ("body", "a", 0, 0)
    assert relay.feed() is None
    problem = relay.problem()
    relay.close()
    assert problem is not None and "boom: no such file" in problem


def test_a_refused_push_retries_and_never_shows_the_key(tmp_path: Path) -> None:
    procs = Procs(tmp_path, [], refuse=True)
    relay = relay_for(procs, tmp_path)

    def retrying() -> bool:
        relay.tick()
        return relay.statuses()["dest"].state == "retrying"

    wait_until(retrying)
    detail = relay.statuses()["dest"].detail
    relay.close()
    assert "stream key" in detail and "Thử lại sau 2 giây" in detail
    assert "stream-key-0001" not in detail
    assert relay.statuses() == {}


def test_lines_split_on_carriage_returns_and_stay_short() -> None:
    seen: list[str] = []
    stream = io.BytesIO(b"frame=1\rframe=2\n" + b"x" * 5000 + b"\nlast words")
    _lines(stream, seen.append)
    assert seen == ["frame=1", "frame=2", "x" * 2000, "last words"]
