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

Feeders run as fast as the CPU allows and are held back by their full stdout
pipe; :mod:`soshop.restream.relay` sends their output on at the pace of the
stream's own clock (PCR). That is why nothing here uses ``-re``, which also
throttles the frames decoded before an ``-ss`` seek point and so would hold
the picture back for as long as the seek offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soshop_server.core.config import Settings

AUDIO_RATE = 44100
HOLD_MAX_S = 6 * 3600
"""A paused frame is re-cued long before this; the cap only bounds a stray process."""

_SILENCE = f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"
_QUIET = ["-hide_banner", "-loglevel", "error", "-nostdin"]


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


def _video_filter(profile: Profile) -> str:
    """Fit any input into the frame: letterboxed, never cropped."""
    w, h = profile.width, profile.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
        f"fps={profile.fps},format=yuv420p"
    )


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
) -> list[str]:
    """Encode ``length_s`` of ``source`` from ``seek_s``, looping it if ``loop``.

    A slot longer than its video repeats the video (``loop``); a video without
    sound gets silence so the stream keeps its audio track.
    """
    args = [ffmpeg, *_QUIET]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-ss", f"{max(seek_s, 0.0):.3f}", "-i", source]
    if has_audio:
        args += ["-map", "0:v:0", "-map", "0:a:0", "-af", "aresample=async=1"]
    else:
        args += ["-f", "lavfi", "-i", _SILENCE, "-map", "0:v:0", "-map", "1:a:0"]
    args += ["-vf", _video_filter(profile)]
    return args + _encode(profile, ts_offset_s, length_s)


def hold_args(ffmpeg: str, still: str | None, *, ts_offset_s: float, profile: Profile) -> list[str]:
    """Encode the still image ``still`` (black if ``None``) with silence until stopped.

    This is what a paused show sends: the platforms drop a stream that goes
    quiet, so a pause keeps the frame it stopped on up, with silence.
    """
    args = [ffmpeg, *_QUIET]
    if still is not None:
        args += ["-loop", "1", "-framerate", str(profile.fps), "-i", still]
    else:
        size = f"{profile.width}x{profile.height}"
        args += ["-f", "lavfi", "-i", f"color=c=black:s={size}:r={profile.fps}"]
    args += ["-f", "lavfi", "-i", _SILENCE, "-map", "0:v:0", "-map", "1:a:0"]
    args += ["-vf", _video_filter(profile)]
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
