"""What each restream stretch wears: card, loudness gain, music (real ffmpeg)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from soshop.restream import card as cards
from soshop.restream.card import ProductCard
from soshop.restream.dresser import Dresser
from soshop.restream.encoder import Profile
from soshop_server.core.config import Settings, settings

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")

CARD = ProductCard("Son kem", "189.000đ", 1, None)


def _has_font() -> bool:
    try:
        cards._font(20)
    except cards.CardError:  # pragma: no cover
        return False
    return True


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return tmp_path


def _make(*args: str) -> None:
    assert FFMPEG is not None
    done = subprocess.run(  # noqa: S603
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")


def _dresser(**changes: object) -> Dresser:
    assert FFMPEG is not None
    return Dresser(Settings(**changes), FFMPEG, Profile())  # type: ignore[arg-type]


def test_a_quiet_clip_is_boosted_once_and_remembered(data_dir: Path) -> None:
    clip = data_dir / "quiet.m4a"
    _make("-f", "lavfi", "-i", "sine=f=440:d=2,volume=0.05", "-c:a", "aac", str(clip))
    dresser = _dresser(RESTREAM_AUDIO_LUFS=-14.0)
    gain = dresser.gain(str(clip))
    assert gain > 5
    clip.unlink()  # cached: not measured again
    assert dresser.gain(str(clip)) == gain
    assert _dresser(RESTREAM_NORMALIZE_AUDIO=False).gain(str(data_dir / "x.m4a")) == 0.0
    assert dresser.dress(None, str(clip), has_audio=False).gain_db == 0.0


@pytest.mark.skipif(not _has_font(), reason="no TrueType font with Vietnamese")
def test_the_card_is_drawn_once_per_product(data_dir: Path) -> None:
    dresser = _dresser(RESTREAM_OVERLAY="middle")
    first = dresser.overlay(CARD)
    assert first is not None and Path(first.path).is_file()
    assert first.path.startswith(str(data_dir))
    assert dresser.overlay(CARD) is first
    assert dresser.overlay(None) is None
    assert _dresser(RESTREAM_OVERLAY="off").overlay(CARD) is None


def test_the_music_bed_needs_a_playable_file(data_dir: Path) -> None:
    missing = _dresser(RESTREAM_MUSIC_FILE=str(data_dir / "none.mp3"))
    missing.prepare()
    assert missing.music is None
    loud, quiet = data_dir / "loud.m4a", data_dir / "quiet.m4a"
    # lavfi's sine is at 1/8 of full scale: x6 makes a loud master, x0.05 a quiet one.
    _make("-f", "lavfi", "-i", "sine=f=220:d=3,volume=6", "-c:a", "aac", str(loud))
    _make("-f", "lavfi", "-i", "sine=f=220:d=3,volume=0.05", "-c:a", "aac", str(quiet))
    good = _dresser(RESTREAM_MUSIC_FILE=str(loud), RESTREAM_MUSIC_VOLUME=0.2)
    good.prepare()
    assert good.music is not None and 2.5 < good.music.length_s < 3.5
    assert good.dress(None, str(loud), has_audio=False).music == good.music
    soft = _dresser(RESTREAM_MUSIC_FILE=str(quiet), RESTREAM_MUSIC_VOLUME=0.2)
    soft.prepare()
    assert soft.music is not None
    # Both tracks are brought to one loudness first: the quiet one gets more volume.
    assert good.music.volume < 0.2 < soft.music.volume
    bed = loud
    muted = _dresser(RESTREAM_MUSIC_FILE=str(bed), RESTREAM_MUSIC_VOLUME=0.0)
    muted.prepare()
    assert muted.music is None
