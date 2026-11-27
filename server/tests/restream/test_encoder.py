"""ffmpeg command lines for the restream feed."""

from __future__ import annotations

from soshop.restream.encoder import HOLD_MAX_S, Profile, hold_args, play_args, push_args
from soshop_server.core.config import Settings

P = Profile()


def _after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


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
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert maps == ["0:v:0", "0:a:0"]  # same layout in every feeder: video, then audio
    assert _after(args, "-g") == "60" and _after(args, "-keyint_min") == "60"
    assert "scale=720:1280" in _after(args, "-vf") and "pad=720:1280" in _after(args, "-vf")


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
    )
    assert _after(args, "-stream_loop") == "-1"
    assert args.index("-stream_loop") < args.index("-ss")
    assert any(a.startswith("anullsrc=") for a in args)
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert maps == ["0:v:0", "1:a:0"]


def test_hold_repeats_the_paused_frame_or_black() -> None:
    still = hold_args("ffmpeg", "still.jpg", ts_offset_s=3, profile=P)
    assert _after(still, "-loop") == "1" and _after(still, "-i") == "still.jpg"
    assert _after(still, "-t") == f"{HOLD_MAX_S:.3f}"
    black = hold_args("ffmpeg", None, ts_offset_s=3, profile=P)
    assert "color=c=black:s=720x1280:r=30" in black


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
