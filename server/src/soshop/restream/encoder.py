"""ffmpeg command lines for the restream feed (pure, no processes).

A broadcast's feed is a chain of short-lived *feeder* processes, each
encoding one stretch of the show to MPEG-TS on its stdout: one slot of a video
from an offset (:func:`play_args`), or the paused frame while the show is
paused (:func:`hold_args`). Every feeder encodes to the same :class:`Profile`
with the same stream layout (video first, then stereo AAC), so their outputs
splice into one continuous stream, and each one starts its timestamps at
``ts_offset_s`` so the spliced clock only moves forward. One *pusher* per
destination copies that stream into FLV over RTMP without re-encoding
(:func:`push_args`).

A feeder can be *dressed* (:class:`Dressing`): the product card laid over the
picture, a gain that brings the clip to a common loudness (measured with
:func:`loudness_args`) and a music bed seeked to the stream's clock so it runs
on across cuts. The output layout stays the same either way.

Feeders run as fast as the CPU allows and are held back by their full stdout
pipe; :mod:`soshop.restream.relay` sends their output on at the pace of the
stream's own clock (PCR). That is why nothing here uses ``-re``, which also
throttles the frames decoded before an ``-ss`` seek point and so would hold
the picture back for as long as the seek offset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soshop_server.core.config import Settings

AUDIO_RATE = 44100
HOLD_MAX_S = 6 * 3600
"""A paused frame is re-cued long before this; the cap only bounds a stray process."""
MAX_BOOST_DB = 15.0
MAX_CUT_DB = 20.0
SILENT_LUFS = -60.0
"""Quieter than this is treated as silence and left alone rather than boosted."""

_SILENCE = f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"
_QUIET = ["-hide_banner", "-loglevel", "error", "-nostdin"]
_LIMIT = "0.891"  # -1 dBFS: a boosted clip never clips
_LOUDNESS_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")


@dataclass(frozen=True)
class Overlay:
    """A PNG drawn over the picture with its top-left corner at ``(x, y)``."""

    path: str
    x: int
    y: int


@dataclass(frozen=True)
class Music:
    """A music bed under the whole stream: one track, looped, at a linear ``volume``."""

    path: str
    length_s: float
    volume: float

    def offset_at(self, ts_offset_s: float) -> float:
        """Where in the track stream time ``ts_offset_s`` falls.

        Feeders start at the stream's own clock, so seeking the track there
        keeps the music running on across cuts instead of restarting.
        """
        return ts_offset_s % self.length_s if self.length_s > 0 else 0.0


@dataclass(frozen=True)
class Dressing:
    """What one stretch gets on top of its source: card, loudness gain, music."""

    overlay: Overlay | None = None
    gain_db: float = 0.0
    music: Music | None = None


PLAIN = Dressing()


@dataclass(frozen=True)
class Profile:
    """The one encoding every feeder of a stream uses (9:16 by default)."""

    width: int = 720
    height: int = 1280
    fps: int = 30
    video_kbps: int = 2500
    audio_kbps: int = 128
    preset: str = "veryfast"

    @classmethod
    def from_settings(cls, settings: Settings) -> Profile:
        def even(value: int) -> int:
            return max(value - value % 2, 2)

        return cls(
            width=even(settings.RESTREAM_WIDTH),
            height=even(settings.RESTREAM_HEIGHT),
            fps=settings.RESTREAM_FPS,
            video_kbps=settings.RESTREAM_VIDEO_KBPS,
            audio_kbps=settings.RESTREAM_AUDIO_KBPS,
            preset=settings.RESTREAM_X264_PRESET,
        )


def _fit(profile: Profile) -> str:
    """Fit any input into the frame: letterboxed, never cropped."""
    w, h = profile.width, profile.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={profile.fps}"
    )


def _dress(
    args: list[str],
    *,
    video: str,
    audio: str,
    profile: Profile,
    dressing: Dressing,
    ts_offset_s: float,
) -> list[str]:
    """``args`` plus the overlay and music inputs, the filter graph and the maps.

    ``video`` and ``audio`` name the source streams (``0:v:0``, ``1:a:0``)
    among the inputs ``args`` already opens. Whatever the dressing, the output
    is one video stream then one audio stream, so feeders still splice.
    """
    args = list(args)
    n = args.count("-i")
    graph = [f"[{video}]{_fit(profile)}[fit]"]
    if dressing.overlay is not None:
        card = dressing.overlay
        args += ["-loop", "1", "-framerate", str(profile.fps), "-i", card.path]
        graph += [
            f"[{n}:v]format=rgba[card]",
            f"[fit][card]overlay={card.x}:{card.y}:format=auto,format=yuv420p[v]",
        ]
        n += 1
    else:
        graph.append("[fit]format=yuv420p[v]")
    sound = f"[{audio}]aresample=async=1"
    if dressing.gain_db:
        sound += f",volume={dressing.gain_db:.1f}dB,alimiter=limit={_LIMIT}:level=0"
    music = dressing.music
    if music is not None:
        seek = f"{music.offset_at(ts_offset_s):.3f}"
        args += ["-stream_loop", "-1", "-ss", seek, "-i", music.path]
        graph += [
            f"{sound}[main]",
            f"[{n}:a:0]volume={music.volume:.3f},aresample={AUDIO_RATE},"
            "aformat=channel_layouts=stereo[bed]",
            "[main][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
        ]
    else:
        graph.append(f"{sound}[a]")
    return [*args, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]"]


def _encode(profile: Profile, ts_offset_s: float, length_s: float) -> list[str]:
    gop = str(profile.fps * 2)  # a keyframe every 2 s, as TikTok and Shopee ask
    kbps = profile.video_kbps
    return [
        "-c:v",
        "libx264",
        "-preset",
        profile.preset,
        "-tune",
        "zerolatency",
        "-profile:v",
        "high",
        "-b:v",
        f"{kbps}k",
        "-maxrate",
        f"{kbps}k",
        "-bufsize",
        f"{kbps * 2}k",
        "-g",
        gop,
        "-keyint_min",
        gop,
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        f"{profile.audio_kbps}k",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
        "-t",
        f"{length_s:.3f}",
        "-output_ts_offset",
        f"{ts_offset_s:.3f}",
        "-f",
        "mpegts",
        "pipe:1",
    ]


def play_args(
    ffmpeg: str,
    source: str,
    *,
    seek_s: float,
    length_s: float,
    loop: bool,
    has_audio: bool,
    ts_offset_s: float,
    profile: Profile,
    dressing: Dressing = PLAIN,
) -> list[str]:
    """Encode ``length_s`` of ``source`` from ``seek_s``, looping it if ``loop``.

    A slot longer than its video repeats the video (``loop``); a video without
    sound gets silence so the stream keeps its audio track. ``dressing``
    adds the product card, the loudness gain and the music bed.
    """
    args = [ffmpeg, *_QUIET]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-ss", f"{max(seek_s, 0.0):.3f}", "-i", source]
    if not has_audio:
        args += ["-f", "lavfi", "-i", _SILENCE]
    if not has_audio and dressing.gain_db:
        dressing = Dressing(dressing.overlay, 0.0, dressing.music)  # no point boosting silence
    args = _dress(
        args,
        video="0:v:0",
        audio="0:a:0" if has_audio else "1:a:0",
        profile=profile,
        dressing=dressing,
        ts_offset_s=ts_offset_s,
    )
    return args + _encode(profile, ts_offset_s, length_s)


def hold_args(
    ffmpeg: str,
    still: str | None,
    *,
    ts_offset_s: float,
    profile: Profile,
    dressing: Dressing = PLAIN,
) -> list[str]:
    """Encode the still image ``still`` (black if ``None``) with silence until stopped.

    This is what a paused show sends: the platforms drop a stream that goes
    quiet, so a pause keeps the frame it stopped on up, with silence (or the
    music bed, if there is one) and the same product card.
    """
    args = [ffmpeg, *_QUIET]
    if still is not None:
        args += ["-loop", "1", "-framerate", str(profile.fps), "-i", still]
    else:
        size = f"{profile.width}x{profile.height}"
        args += ["-f", "lavfi", "-i", f"color=c=black:s={size}:r={profile.fps}"]
    args += ["-f", "lavfi", "-i", _SILENCE]
    quiet = Dressing(dressing.overlay, 0.0, dressing.music)
    args = _dress(
        args, video="0:v:0", audio="1:a:0", profile=profile, dressing=quiet, ts_offset_s=ts_offset_s
    )
    return args + _encode(profile, ts_offset_s, HOLD_MAX_S)


def still_args(ffmpeg: str, source: str, *, at_s: float, out: str) -> list[str]:
    """Grab the frame of ``source`` at ``at_s`` into the JPEG ``out``."""
    return [
        ffmpeg,
        *_QUIET,
        "-y",
        "-ss",
        f"{max(at_s, 0.0):.3f}",
        "-i",
        source,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        out,
    ]


def loudness_args(ffmpeg: str, source: str) -> list[str]:
    """Measure the integrated loudness of ``source``'s audio (EBU R128, on stderr)."""
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-i",
        source,
        "-map",
        "0:a:0",
        "-af",
        "ebur128=framelog=quiet",
        "-f",
        "null",
        "-",
    ]


def parse_loudness(stderr: str) -> float | None:
    """The integrated loudness in LUFS from :func:`loudness_args` output."""
    found = _LOUDNESS_RE.findall(stderr)
    return float(found[-1]) if found else None


def gain_for(measured_lufs: float | None, target_lufs: float) -> float:
    """dB that bring a clip to ``target_lufs``, within limits; 0 for silence or unknown."""
    if measured_lufs is None or measured_lufs < SILENT_LUFS:
        return 0.0
    gain = max(-MAX_CUT_DB, min(MAX_BOOST_DB, target_lufs - measured_lufs))
    return round(gain, 1)


def push_args(ffmpeg: str, url: str) -> list[str]:
    """Copy the MPEG-TS feed on stdin to ``url`` as FLV (RTMP), no re-encode.

    Where one feeder hands over to the next, the last packet before the cut
    arrives incomplete or out of sequence; ``discardcorrupt`` drops it rather
    than sending a broken frame to the platform. Progress goes to stdout every
    second: a growing ``total_size`` means the platform accepted the stream.
    """
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-stats_period",
        "1",
        "-fflags",
        "+discardcorrupt",
        "-analyzeduration",
        "2000000",
        "-f",
        "mpegts",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        url,
    ]
