"""The processes behind one broadcast's restream, and the pacing between them.

A :class:`Relay` owns one broadcast's feed:

- :class:`Segment`\\ s, one encoder process each (:mod:`~soshop.restream.encoder`):
  the stretch being sent and at most one queued behind it. An encoder runs as
  fast as the CPU allows until :data:`BUFFER_S` of its output waits unsent,
  then blocks on its stdout pipe;
- a *pacer* thread that sends the current segment's MPEG-TS packets on at the
  pace of their PCR (the stream's own clock), :data:`LEAD_S` ahead of real
  time, and moves to the queued segment the moment the current one runs dry.
  The mapping from stream time to wall time carries over, so that splice has
  no gap and the feed never drifts from the show;
- one :class:`Pusher` per destination, an ffmpeg copying the paced stream to
  RTMP, restarted with backoff when it fails.

Everything is threads and blocking pipes rather than asyncio subprocesses, so
it runs under any event loop (uvicorn may use the Windows selector loop, which
cannot start subprocesses). The runner calls the blocking methods through
``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from soshop.restream import encoder
from soshop.restream.encoder import Profile
from soshop.restream.plan import Cue, Failure, Feed
from soshop.restream.platforms import OutputStatus, describe_failure, redact

logger = logging.getLogger(__name__)

TS_PACKET = 188
PCR_HZ = 27_000_000
LEAD_S = 0.3
"""Send each packet this long before its time, to keep the platforms' buffers fed."""
BEHIND_S = 1.0
"""Further behind than this (a slow encoder), the clock is reset rather than caught up."""
BUFFER_S = 4.0
"""Encoded output held per segment before its encoder is made to wait."""
FRESH_GAP_S = 0.2
"""Timestamp gap after a cut, so no packet of the new stretch predates the old one."""
SPLICE_GAP_S = 0.04
STEADY_S = 60.0
"""Live this long, a pusher's failure count starts over."""
BACKOFF_S = (2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
BACKLOG_S = 10.0
"""A pusher this far behind (a slow upload) is restarted rather than left to lag."""
CLOSE_WAIT_S = 3.0
PROBLEM_S = 15.0
"""How long an encoder failure stays the explanation for a feed that is not sending."""

_READ = TS_PACKET * 87
_TAIL_LINES = 12
_LINE_MAX = 2000
_LINE_BREAK = re.compile(rb"[\r\n]+")

Spawn = Callable[[list[str], bool], "subprocess.Popen[bytes]"]
"""Start ``argv``: a pusher (``True``: stdin and stdout piped) or an encoder (stdout piped)."""


def _spawn(argv: list[str], stdin: bool) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wall_ms() -> int:
    return int(time.time() * 1000)


def packet_pcr(data: bytes | memoryview, at: int) -> int | None:
    """The PCR of the TS packet at ``data[at:]``, in 27 MHz ticks, if it carries one."""
    if data[at] != 0x47 or not data[at + 3] & 0x20:  # sync byte, adaptation field
        return None
    if data[at + 4] < 7 or not data[at + 5] & 0x10:  # long enough, PCR flag
        return None
    b = data[at + 6 : at + 12]
    base = (b[0] << 25) | (b[1] << 17) | (b[2] << 9) | (b[3] << 1) | (b[4] >> 7)
    return base * 300 + (((b[4] & 0x01) << 8) | b[5])


def _lines(stream: IO[bytes] | None, take: Callable[[str], None]) -> None:
    """Hand each line ffmpeg writes to ``take`` until it closes the pipe.

    Splits on ``\\r`` as well as ``\\n`` and caps a line's length, so a status
    line rewritten in place can never grow without bound. Somebody has to read
    the pipe either way: ffmpeg blocks once it is full.
    """
    if stream is None:
        return
    reader: Any = stream
    rest = b""
    with contextlib.suppress(OSError, ValueError):
        while data := reader.read1(4096):
            *done, rest = _LINE_BREAK.split(rest + data)
            rest = rest[-_LINE_MAX:]
            for raw in done:
                if raw:
                    take(raw[:_LINE_MAX].decode("utf-8", errors="replace"))
        if rest:
            take(rest.decode("utf-8", errors="replace"))


def _read_tail(stream: IO[bytes] | None, tail: deque[str]) -> None:
    """Keep the last lines ffmpeg wrote to stderr, for when it fails."""

    def keep(line: str) -> None:
        if "corrupt" not in line.lower():  # expected at every splice
            tail.append(line.strip())

    _lines(stream, keep)


def _thread(target: Callable[..., None], *args: Any, name: str) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread


# ------------------------------------------------------------------ segments


class Segment:
    """One encoder process and the output it has produced but not yet sent."""

    def __init__(
        self,
        cue: Cue,
        argv: list[str],
        *,
        ts_offset_s: float,
        max_buffer: int,
        started_ms: int,
        spawn: Spawn,
    ) -> None:
        self.cue = cue
        self.ts_offset_s = ts_offset_s
        self.length_s = cue.remaining_ms / 1000 if cue.kind == "play" else encoder.HOLD_MAX_S
        self.started_ms = started_ms
        self.first_pcr: int | None = None
        self.sent_pcr: int | None = None
        self.produced = 0
        self.tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self._max = max_buffer
        self._cond = threading.Condition()
        self._chunks: deque[bytes] = deque()
        self._buffered = 0
        self._eof = False
        self._closed = False
        self._proc = spawn(argv, False)
        _thread(self._pump, name="restream-encoder")
        self._log = _thread(_read_tail, self._proc.stderr, self.tail, name="restream-encoder-log")

    @property
    def encoded(self) -> bool:
        """The encoder is done: everything it will produce is buffered or sent."""
        with self._cond:
            return self._eof

    @property
    def drained(self) -> bool:
        with self._cond:
            return (self._eof or self._closed) and not self._chunks

    def stream_s(self, pcr: int) -> float:
        """Stream time of ``pcr``: the segment's offset plus its media time so far."""
        first = self.first_pcr if self.first_pcr is not None else pcr
        return self.ts_offset_s + (pcr - first) / PCR_HZ

    def offset_ms(self) -> int:
        """How far into its slot this segment has been sent."""
        if self.cue.kind == "hold" or self.sent_pcr is None or self.first_pcr is None:
            return self.cue.start_ms
        sent = self.cue.start_ms + (self.sent_pcr - self.first_pcr) // 27_000
        return min(sent, self.cue.position.length_ms)

    def _pump(self) -> None:
        stdout = self._proc.stdout
        rest = b""
        try:
            while stdout is not None:
                data = stdout.read1(_READ)  # type: ignore[attr-defined]
                if not data:
                    break
                data = rest + data
                cut = len(data) - len(data) % TS_PACKET
                rest = data[cut:]
                if cut and not self._put(data[:cut]):
                    break
        except (OSError, ValueError):
            pass
        finally:
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    def _put(self, chunk: bytes) -> bool:
        with self._cond:
            while self._buffered >= self._max and not self._closed:
                self._cond.wait()
            if self._closed:
                return False
            self._chunks.append(chunk)
            self._buffered += len(chunk)
            self.produced += len(chunk)
            self._cond.notify_all()
            return True

    def take(self, timeout: float) -> bytes | None:
        """The next chunk of output, or ``None`` if there is none within ``timeout``."""
        with self._cond:
            if not self._chunks and not self._eof and not self._closed:
                self._cond.wait(timeout)
            if not self._chunks:
                return None
            chunk = self._chunks.popleft()
            self._buffered -= len(chunk)
            self._cond.notify_all()
            return chunk

    def failed(self) -> bool:
        """The encoder exited without producing anything."""
        return self.encoded and self.produced == 0

    def reason(self) -> str:
        """The encoder's last error line, once it has exited (waits for it briefly)."""
        self._log.join(timeout=1.0)
        return self.tail[-1] if self.tail else "không rõ lỗi"

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._chunks.clear()
            self._buffered = 0
            self._cond.notify_all()
        with contextlib.suppress(OSError):
            self._proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            self._proc.wait(timeout=2)


# ------------------------------------------------------------------- pushers


class Pusher:
    """One ffmpeg copying the paced feed to one RTMP ingest, restarted when it fails.

    It counts as live once ffmpeg reports bytes written to the server (its
    ``-progress`` output), which only happens after the platform accepted the
    connection and the stream key.
    """

    def __init__(
        self,
        ffmpeg: str,
        url: str,
        secrets: tuple[str, ...],
        *,
        max_backlog: int,
        spawn: Spawn = _spawn,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self._argv = encoder.push_args(ffmpeg, url)
        # Hide the key; a URL with no separate key may carry one inside, so hide it whole.
        self._secrets = secrets if secrets else (url,)
        self._max_backlog = max_backlog
        self._spawn_fn = spawn
        self._mono = mono
        self._cond = threading.Condition()
        self._backlog: deque[bytes] = deque()
        self._backlog_bytes = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self._log: threading.Thread | None = None
        self._status = OutputStatus("connecting")
        self._failures = 0
        self._retry_at = 0.0
        self._started = 0.0
        self._accepted = False
        self._stuck: str | None = None
        self._closed = False

    @property
    def status(self) -> OutputStatus:
        with self._cond:
            return self._status

    def feed(self, data: bytes) -> None:
        """Queue ``data`` for the process; dropped while it is down."""
        with self._cond:
            if self._closed or self._proc is None:
                return
            if self._backlog_bytes + len(data) > self._max_backlog:
                self._stuck = "Mạng tải lên không đủ nhanh cho bitrate này, đang kết nối lại."
                with contextlib.suppress(OSError):
                    self._proc.kill()
                return
            self._backlog.append(data)
            self._backlog_bytes += len(data)
            self._cond.notify_all()

    def tick(self) -> None:
        """Start the process, notice it died, or mark it live. Call often."""
        with self._cond:
            if self._closed:
                return
            now = self._mono()
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                self._exited(proc, now)
                proc = None
            if proc is None:
                if now >= self._retry_at:
                    self._start(now)
                return
            if self._accepted and self._status.state != "live":
                self._status = OutputStatus("live")
            if self._accepted and now - self._started >= STEADY_S:
                self._failures = 0

    def close(self) -> None:
        """End the stream: a live one sends what it has and closes cleanly first."""
        with self._cond:
            self._closed = True
            proc = self._proc
            live = self._status.state == "live"
            self._status = OutputStatus("off")
            self._cond.notify_all()
        if proc is None:
            return
        try:
            proc.wait(timeout=CLOSE_WAIT_S if live else 0.01)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=2)

    def _start(self, now: float) -> None:
        try:
            proc = self._spawn_fn(self._argv, True)
        except OSError as exc:
            self._fail(now, f"Không chạy được ffmpeg: {exc}")
            return
        self._proc = proc
        self._started = now
        self._accepted = False
        self._stuck = None
        self._backlog.clear()
        self._backlog_bytes = 0
        self._tail.clear()
        if self._status.state != "retrying":
            self._status = OutputStatus("connecting")
        _thread(self._write, proc, name="restream-pusher")
        _thread(self._progress, proc, name="restream-pusher-progress")
        self._log = _thread(_read_tail, proc.stderr, self._tail, name="restream-pusher-log")

    def _exited(self, proc: subprocess.Popen[bytes], now: float) -> None:
        if self._log is not None:
            self._log.join(timeout=1.0)  # its last words are the reason
        tail = redact("\n".join(self._tail), *self._secrets)
        reason = self._stuck or describe_failure(tail, proc.returncode)
        logger.warning("restream push to %s stopped: %s", redact(self.url, *self._secrets), tail)
        self._proc = None
        self._cond.notify_all()
        self._fail(now, reason)

    def _fail(self, now: float, reason: str) -> None:
        delay = BACKOFF_S[min(self._failures, len(BACKOFF_S) - 1)]
        self._failures += 1
        self._retry_at = now + delay
        self._status = OutputStatus("retrying", f"{reason} Thử lại sau {delay:.0f} giây.")

    def _progress(self, proc: subprocess.Popen[bytes]) -> None:
        def take(line: str) -> None:
            key, _, value = line.partition("=")
            if key.strip() == "total_size" and value.strip().isdigit() and int(value) > 0:
                with self._cond:
                    if self._proc is proc:
                        self._accepted = True

        _lines(proc.stdout, take)

    def _next(self, proc: subprocess.Popen[bytes]) -> bytes | None:
        with self._cond:
            while not self._backlog and self._proc is proc and not self._closed:
                self._cond.wait(1.0)
            if self._proc is not proc or not self._backlog:
                return None
            data = self._backlog.popleft()
            self._backlog_bytes -= len(data)
            return data

    def _write(self, proc: subprocess.Popen[bytes]) -> None:
        stdin = proc.stdin
        try:
            while stdin is not None and (data := self._next(proc)) is not None:
                stdin.write(data)
        except (OSError, ValueError):
            pass
        finally:
            if stdin is not None:
                with contextlib.suppress(OSError, ValueError):
                    stdin.close()


# --------------------------------------------------------------------- relay


@dataclass(frozen=True)
class Output:
    url: str
    secrets: tuple[str, ...] = ()


class Relay:
    """One broadcast's feed: its encoder segments, the pacer and the pushers."""

    def __init__(
        self,
        ffmpeg: str,
        profile: Profile,
        work: Path,
        *,
        spawn: Spawn = _spawn,
        mono: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = _wall_ms,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._profile = profile
        self.work = work
        """Scratch space for this relay (the paused frame); the owner removes it."""
        self._spawn = spawn
        self._mono = mono
        self._wall_ms = wall_ms
        rate = (profile.video_kbps + profile.audio_kbps) * 125  # bytes per second
        self._max_buffer = int(rate * BUFFER_S)
        self._max_backlog = int(rate * BACKLOG_S)
        self._cond = threading.Condition()
        self._gen = 0
        self._closed = False
        self._current: Segment | None = None
        self._queued: Segment | None = None
        self._pushers: dict[str, Pusher] = {}
        self._anchor: tuple[float, float] | None = None
        """``(monotonic, stream seconds)`` that are the same instant."""
        self._sent_s = 0.0
        self._failure: Failure | None = None
        self._problem: tuple[str, float] | None = None
        self._stills = 0
        _thread(self._pace, name="restream-pacer")

    # -------------------------------------------------------- what is on air

    def feed(self) -> Feed | None:
        with self._cond:
            seg = self._current
            if seg is None:
                return None
            return Feed(
                cue=seg.cue,
                offset_ms=seg.offset_ms(),
                started_ms=seg.started_ms,
                sending=seg.sent_pcr is not None,
                encoded=seg.encoded,
                queued=self._queued.cue if self._queued is not None else None,
            )

    def failure(self) -> Failure | None:
        with self._cond:
            return self._failure

    def problem(self) -> str | None:
        """Why nothing is being sent, if an encoder failed recently."""
        with self._cond:
            if self._problem is None or self._mono() - self._problem[1] > PROBLEM_S:
                return None
            sending = self._current is not None and self._current.sent_pcr is not None
            return None if sending else self._problem[0]

    def note_failure(self, cue: Cue, reason: str) -> None:
        """Record that ``cue`` could not be started (its file is missing, say)."""
        with self._cond:
            self._failure = Failure(cue.slot, self._wall_ms())
            self._problem = (reason, self._mono())

    # ----------------------------------------------------------- the encoder

    def start(self, cue: Cue, source: str, has_audio: bool) -> None:
        """Drop what the feed holds and encode ``cue`` from now. Blocking."""
        still = self._still(cue, source) if cue.kind == "hold" else None
        with self._cond:
            if self._closed:
                return
            dropped = [s for s in (self._current, self._queued) if s is not None]
            self._current = self._queued = None
            self._gen += 1
            self._cond.notify_all()
            ts = self._sent_s + FRESH_GAP_S
            if self._anchor is not None:
                ts = max(ts, self._anchor[1] + (self._mono() - self._anchor[0]))
        for old in dropped:
            old.close()
        fresh = self._segment(cue, source, has_audio, still, ts)
        with self._cond:
            if fresh is None or self._closed or self._current is not None:
                if fresh is not None:
                    fresh.close()
                return
            self._current = fresh
            self._cond.notify_all()

    def queue(self, cue: Cue, source: str, has_audio: bool) -> None:
        """Encode ``cue`` to follow the current stretch without a gap. Blocking."""
        with self._cond:
            current = self._current
            if self._closed or current is None or self._queued is not None:
                return
            ts = current.ts_offset_s + current.length_s + SPLICE_GAP_S
        seg = self._segment(cue, source, has_audio, None, ts)
        with self._cond:
            if seg is None:
                return
            if self._closed or self._current is not current or self._queued is not None:
                seg.close()
                return
            self._queued = seg

    def _segment(
        self, cue: Cue, source: str, has_audio: bool, still: str | None, ts: float
    ) -> Segment | None:
        if cue.kind == "hold":
            argv = encoder.hold_args(self._ffmpeg, still, ts_offset_s=ts, profile=self._profile)
        else:
            argv = encoder.play_args(
                self._ffmpeg,
                source,
                seek_s=cue.seek_ms / 1000,
                length_s=cue.remaining_ms / 1000,
                loop=cue.loops,
                has_audio=has_audio,
                ts_offset_s=ts,
                profile=self._profile,
            )
        try:
            return Segment(
                cue,
                argv,
                ts_offset_s=ts,
                max_buffer=self._max_buffer,
                started_ms=self._wall_ms(),
                spawn=self._spawn,
            )
        except OSError as exc:
            self.note_failure(cue, f"Không chạy được ffmpeg: {exc}")
            return None

    def _still(self, cue: Cue, source: str) -> str | None:
        """The paused frame as a JPEG, or ``None`` (black) if it cannot be grabbed."""
        self._stills += 1
        self.work.mkdir(parents=True, exist_ok=True)
        out = self.work / f"still-{self._stills % 2}.jpg"
        argv = encoder.still_args(self._ffmpeg, source, at_s=cue.seek_ms / 1000, out=str(out))
        try:
            done = subprocess.run(argv, capture_output=True, timeout=20, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired):
            return None
        return str(out) if done.returncode == 0 and out.is_file() else None

    # ------------------------------------------------------------ the pacer

    def _pace(self) -> None:
        while True:
            with self._cond:
                while self._current is None and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                seg, gen = self._current, self._gen
            assert seg is not None  # noqa: S101 - narrowed under the lock above
            chunk = seg.take(0.25)
            if chunk is not None:
                self._send(seg, gen, chunk)
            elif seg.drained:
                self._advance(seg)

    def _advance(self, seg: Segment) -> None:
        """``seg`` has nothing more: splice on the queued segment, if there is one."""
        reason = seg.reason() if seg.failed() else None
        with self._cond:
            if self._current is not seg:
                return
            if reason is not None:
                self._failure = Failure(seg.cue.slot, self._wall_ms())
                self._problem = (f"Không mã hóa được video: {reason[:200]}", self._mono())
            self._current, self._queued = self._queued, None
        seg.close()

    def _send(self, seg: Segment, gen: int, chunk: bytes) -> None:
        view = memoryview(chunk)
        start = 0
        last: int | None = None
        for at in range(0, len(chunk), TS_PACKET):
            pcr = packet_pcr(view, at)
            if pcr is None:
                continue
            if seg.first_pcr is None:
                seg.first_pcr = pcr
            due = self._due(seg.stream_s(pcr)) - LEAD_S
            if due > self._mono():
                if not self._emit(seg, gen, view[start:at], last) or not self._sleep(due, gen):
                    return
                start = at
            last = pcr
        self._emit(seg, gen, view[start:], last)

    def _due(self, stream_s: float) -> float:
        """The monotonic time stream time ``stream_s`` is due on air."""
        now = self._mono()
        anchor = self._anchor
        if anchor is None or stream_s < anchor[1]:
            anchor = self._anchor = (now, stream_s)
        due = anchor[0] + (stream_s - anchor[1])
        if due < now - BEHIND_S:
            self._anchor = (now, stream_s)
            return now
        return due

    def _sleep(self, until: float, gen: int) -> bool:
        """Wait until ``until`` unless the feed is cut meanwhile. True if still current."""
        with self._cond:
            while self._gen == gen and not self._closed:
                left = until - self._mono()
                if left <= 0:
                    return True
                self._cond.wait(left)
            return False

    def _emit(self, seg: Segment, gen: int, data: memoryview, pcr: int | None) -> bool:
        with self._cond:
            if self._gen != gen or self._closed or self._current is not seg:
                return False
            pushers = list(self._pushers.values())
            if pcr is not None:
                seg.sent_pcr = pcr
                self._sent_s = max(self._sent_s, seg.stream_s(pcr))
        if len(data):
            payload = bytes(data)
            for pusher in pushers:
                pusher.feed(payload)
        return True

    # ----------------------------------------------------------- the pushers

    def set_outputs(self, outputs: Mapping[str, Output]) -> None:
        """Push to exactly ``outputs`` (destination id to ingest). Blocking."""
        stale: list[Pusher] = []
        with self._cond:
            if self._closed:
                return
            for dest_id, pusher in list(self._pushers.items()):
                wanted = outputs.get(dest_id)
                if wanted is None or wanted.url != pusher.url:
                    stale.append(self._pushers.pop(dest_id))
            for dest_id, wanted in outputs.items():
                if dest_id not in self._pushers:
                    self._pushers[dest_id] = Pusher(
                        self._ffmpeg,
                        wanted.url,
                        wanted.secrets,
                        max_backlog=self._max_backlog,
                        spawn=self._spawn,
                        mono=self._mono,
                    )
        for pusher in stale:
            pusher.close()

    def tick(self) -> None:
        """Let every pusher start, restart or go live. Blocking (may spawn)."""
        with self._cond:
            pushers = list(self._pushers.values())
        for pusher in pushers:
            pusher.tick()

    def statuses(self) -> dict[str, OutputStatus]:
        with self._cond:
            pushers = dict(self._pushers)
        return {dest_id: pusher.status for dest_id, pusher in pushers.items()}

    def close(self) -> None:
        """Stop encoding, end every stream cleanly and stop the pacer. Blocking."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._gen += 1
            segments = [s for s in (self._current, self._queued) if s is not None]
            pushers = list(self._pushers.values())
            self._current = self._queued = None
            self._pushers.clear()
            self._cond.notify_all()
        for seg in segments:
            seg.close()
        for pusher in pushers:
            pusher.close()
