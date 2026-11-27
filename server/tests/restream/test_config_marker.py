"""When the TikTok settings are written over what /studio/channels holds."""

from __future__ import annotations

from pathlib import Path

from soshop.restream.runner import config_changed, config_stamp, remember_config


def test_settings_apply_only_when_they_change(tmp_path: Path) -> None:
    marker = tmp_path / "restream" / "tiktok-config.sha256"
    first = config_stamp("rtmp://h/game/", "key-1")
    assert config_changed(marker, first)  # never applied: apply
    remember_config(marker, first)
    assert not config_changed(marker, first)  # restart, same .env: keep studio edits
    assert config_changed(marker, config_stamp("rtmp://h/game/", "key-2"))  # new key in .env
    assert config_changed(marker, config_stamp("rtmp://other/game/", "key-1"))
    assert "key-1" not in marker.read_text(encoding="ascii")
