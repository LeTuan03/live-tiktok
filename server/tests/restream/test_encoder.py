"""ffmpeg command lines for the restream feed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from soshop.restream.encoder import (
    HOLD_MAX_S,
    MAX_BOOST_DB,
    MAX_CUT_DB,
    Dressing,
    Music,
    Overlay,
    Profile,
    gain_for,
    hold_args,
    loudness_args,
    parse_loudness,
    play_args,
    push_args,
)
from soshop_server.core.config import Settings

P = Profile()
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def _maps(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-map"]


def _inputs(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-i"]


def test_play_seeks_encodes_the_slot_and_offsets_its_timestamps() -> None:
    args = play_args(
        "ffmpeg",
        "v.mp4",
        seek_s=2.5,
        length_s=7.25,
        loop=False,
        has_audio=True,
        ts_offset_s=12.04,
        profile=P,
    )
    assert args[0] == "ffmpeg" and "-re" not in args and "-stream_loop" not in args
    assert _after(args, "-ss") == "2.500" and _after(args, "-i") == "v.mp4"
    assert args.index("-ss") < args.index("-i")  # input seek: fast, frame accurate
    assert _after(args, "-t") == "7.250" and _after(args, "-output_ts_offset") == "12.040"
    assert args[-3:] == ["-f", "mpegts", "pipe:1"]
    assert _maps(args) == ["[v]", "[a]"]  # same layout in every feeder: video, then audio
    assert _after(args, "-g") == "60" and _after(args, "-keyint_min") == "60"
    graph = _after(args, "-filter_complex")
    assert graph.startswith("[0:v:0]scale=720:1280") and "pad=720:1280" in graph
    assert "[0:a:0]aresample=async=1[a]" in graph
    assert "overlay" not in graph and "volume" not in graph and "amix" not in graph


def test_a_long_slot_loops_and_a_silent_video_gets_silence() -> None:
    args = play_args(
        "ffmpeg",
        "v.mp4",
        seek_s=0,
        length_s=20,
        loop=True,
        has_audio=False,
        ts_offset_s=0,
        profile=P,
        dressing=Dressing(gain_db=6.0),
    )
    assert _after(args, "-stream_loop") == "-1"
    assert args.index("-stream_loop") < args.index("-ss")
    assert any(a.startswith("anullsrc=") for a in args)
    graph = _after(args, "-filter_complex")
    assert "[1:a:0]aresample=async=1[a]" in graph
    assert "volume" not in graph  # silence is never boosted


def test_dressing_adds_the_card_the_gain_and_the_music_bed() -> None:
    dressing = Dressing(
        overlay=Overlay("card.png", 10, 159),
        gain_db=-4.25,
        music=Music("bed.mp3", 100.0, 0.15),
    )
    args = play_args(
        "ffmpeg",
        "v.mp4",
        seek_s=1,
        length_s=8,
        loop=False,
        has_audio=True,
        ts_offset_s=250.5,
        profile=P,
        dressing=dressing,
    )
    assert _inputs(args) == ["v.mp4", "card.png", "bed.mp3"]
    assert args[args.index("card.png") - 5 : args.index("card.png")] == [
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
    ]
    # The bed picks up where the stream's clock is, so it plays on across cuts.
    assert args[args.index("bed.mp3") - 5 : args.index("bed.mp3")] == [
        "-stream_loop",
        "-1",
        "-ss",
        "50.500",
        "-i",
    ]
    graph = _after(args, "-filter_complex")
    assert "[1:v]format=rgba[card]" in graph
    assert "[fit][card]overlay=10:159:format=auto,format=yuv420p[v]" in graph
    assert "volume=-4.2dB,alimiter=limit=0.891:level=0[main]" in graph
    assert "[2:a:0]volume=0.150" in graph and "amix=inputs=2:duration=first" in graph
    assert _maps(args) == ["[v]", "[a]"]


def test_hold_repeats_the_paused_frame_or_black() -> None:
    still = hold_args("ffmpeg", "still.jpg", ts_offset_s=3, profile=P)
    assert _after(still, "-loop") == "1" and _after(still, "-i") == "still.jpg"
    assert _after(still, "-t") == f"{HOLD_MAX_S:.3f}"
    black = hold_args("ffmpeg", None, ts_offset_s=3, profile=P)
    assert "color=c=black:s=720x1280:r=30" in black


def test_hold_keeps_the_card_and_music_but_not_the_gain() -> None:
    dressing = Dressing(Overlay("card.png", 1, 2), 9.0, Music("bed.mp3", 10.0, 0.2))
    args = hold_args("ffmpeg", "still.jpg", ts_offset_s=23.0, profile=P, dressing=dressing)
    assert _inputs(args)[-2:] == ["card.png", "bed.mp3"]
    graph = _after(args, "-filter_complex")
    assert "overlay=1:2" in graph and "amix" in graph and "dB" not in graph
    assert _after(args[args.index("-stream_loop") :], "-ss") == "3.000"


def test_loudness_is_read_from_the_ebur128_summary() -> None:
    args = loudness_args("ffmpeg", "v.mp4")
    assert _after(args, "-i") == "v.mp4" and _after(args, "-af") == "ebur128=framelog=quiet"
    stderr = (
        "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
        "    I:         -23.4 LUFS\n    Threshold: -33.6 LUFS\n"
    )
    assert parse_loudness(stderr) == -23.4
    assert parse_loudness("no audio here") is None


def test_gain_brings_clips_to_the_target_within_limits() -> None:
    assert gain_for(-23.0, -14.0) == 9.0
    assert gain_for(-8.0, -14.0) == -6.0
    assert gain_for(-45.0, -14.0) == MAX_BOOST_DB
    assert gain_for(10.0, -14.0) == -MAX_CUT_DB
    assert gain_for(-70.0, -14.0) == 0.0  # silence stays silence
    assert gain_for(None, -14.0) == 0.0


def test_music_offset_wraps_around_the_track() -> None:
    bed = Music("bed.mp3", 30.0, 0.1)
    assert bed.offset_at(0) == 0 and bed.offset_at(45.0) == 15.0
    assert Music("x", 0.0, 0.1).offset_at(12.0) == 0.0


def test_push_copies_the_feed_to_rtmp_without_reencoding() -> None:
    args = push_args("ffmpeg", "rtmp://h/app/key")
    assert args[-1] == "rtmp://h/app/key"
    assert _after(args, "-i") == "pipe:0" and _after(args, "-c") == "copy"
    assert _after(args, "-f") == "mpegts" and args[-5:-1] == [
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
    ]
    assert "+discardcorrupt" in args


def test_profile_from_settings_keeps_frame_sizes_even() -> None:
    config = Settings(
        RESTREAM_WIDTH=1081,
        RESTREAM_HEIGHT=1920,
        RESTREAM_FPS=25,
        RESTREAM_VIDEO_KBPS=4500,
        RESTREAM_X264_PRESET="faster",
    )
    profile = Profile.from_settings(config)
    assert (profile.width, profile.height, profile.fps) == (1080, 1920, 25)
    assert (profile.video_kbps, profile.preset) == (4500, "faster")


# ------------------------------------------------- the real ffmpeg, end to end


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)  # noqa: S603


def _make(*args: str) -> None:
    assert FFMPEG is not None
    done = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args])
    assert done.returncode == 0, done.stderr.decode(errors="replace")


@needs_ffmpeg
def test_a_fully_dressed_feeder_runs_and_keeps_its_layout(tmp_path: Path) -> None:
    assert FFMPEG is not None
    clip, bed, card = tmp_path / "clip.mp4", tmp_path / "bed.m4a", tmp_path / "card.png"
    _make(
        *("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3"),
        *("-f", "lavfi", "-i", "sine=f=440:d=3,volume=0.05"),
        *("-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip)),
    )
    _make("-f", "lavfi", "-i", "sine=f=220:d=4", "-c:a", "aac", str(bed))
    _make("-f", "lavfi", "-i", "color=c=white:s=300x120", "-frames:v", "1", str(card))

    measured = parse_loudness(_run(loudness_args(FFMPEG, str(clip))).stderr.decode())
    assert measured is not None and measured < -30  # a quiet clip
    gain = gain_for(measured, -14.0)
    dressing = Dressing(Overlay(str(card), 20, 160), gain, Music(str(bed), 4.0, 0.15))
    args = play_args(
        FFMPEG,
        str(clip),
        seek_s=0.5,
        length_s=2.0,
        loop=False,
        has_audio=True,
        ts_offset_s=10.0,
        profile=P,
        dressing=dressing,
    )
    done = _run(args)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    out = tmp_path / "out.ts"
    out.write_bytes(done.stdout)
    info = _run([FFMPEG, "-hide_banner", "-i", str(out)]).stderr.decode(errors="replace")
    streams = [line for line in info.splitlines() if "Stream #" in line]
    assert len(streams) == 2
    assert "Video: h264" in streams[0] and "720x1280" in streams[0]
    assert "Audio: aac" in streams[1] and "44100 Hz, stereo" in streams[1]
