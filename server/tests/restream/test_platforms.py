"""Destination validation, ingest URLs and keeping stream keys out of sight."""

from __future__ import annotations

import pytest

from soshop.restream.platforms import (
    DestinationError,
    describe_failure,
    ingest_url,
    mask_key,
    needs_key,
    normalize_key,
    normalize_server_url,
    redact,
)


@pytest.mark.parametrize(
    "raw",
    [
        "rtmp://push-rtmp-l1-sg01.tiktokcdn.com/game/",
        "  rtmps://live-push.shopee.vn/live  ",
        "RTMP://a.rtmp.youtube.com/live2",
        "rtmp://127.0.0.1:1935/live",
    ],
)
def test_server_urls_that_ffmpeg_can_publish_to(raw: str) -> None:
    assert normalize_server_url(raw) == raw.strip()


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("", "Server URL"),
        ("https://live.tiktok.com/studio", "rtmp://"),
        ("rtmp:///live", "máy chủ"),
        ("rtmp://host/live key", "khoảng trắng"),
    ],
)
def test_server_urls_that_are_refused(raw: str, fragment: str) -> None:
    with pytest.raises(DestinationError, match=fragment):
        normalize_server_url(raw)


def test_stream_keys_are_trimmed_and_never_hold_whitespace() -> None:
    assert normalize_key("  abc-123?sign=x \n") == "abc-123?sign=x"
    assert normalize_key("") == ""
    with pytest.raises(DestinationError):
        normalize_key("abc 123")


def test_ingest_url_joins_server_and_key_with_one_slash() -> None:
    assert ingest_url("rtmp://h/game/", "k1") == "rtmp://h/game/k1"
    assert ingest_url("rtmp://h/game", "/k1") == "rtmp://h/game/k1"
    assert ingest_url("rtmp://h/live/full-key-inside", "") == "rtmp://h/live/full-key-inside"


def test_only_a_custom_destination_may_go_without_a_key() -> None:
    assert needs_key("tiktok") and needs_key("shopee") and needs_key("youtube")
    assert not needs_key("custom")


def test_keys_are_masked_to_their_last_characters() -> None:
    assert mask_key("") == ""
    assert mask_key("short-key") == "••••"
    assert mask_key("stream-1234567890-abcd") == "••••abcd"


def test_redact_hides_every_secret_longest_first() -> None:
    url = "rtmp://h/game/stream-secret-key"
    text = f"[rtmp] {url}: Input/output error; key stream-secret-key"
    hidden = redact(text, "stream-secret-key", url, "", "abc")
    assert "stream-secret-key" not in hidden
    assert hidden.startswith("[rtmp] ••••: Input/output error")
    assert redact("abc", "abc") == "abc"  # too short to be a key worth hiding


def test_failures_are_explained_in_terms_of_url_or_key() -> None:
    refused = describe_failure("rtmp://••••: Connection refused", 1)
    assert "Server URL" in refused
    dropped = describe_failure("Error writing trailer: Input/output error", 1)
    assert "stream key" in dropped
    other = describe_failure("first\nUnknown encoder 'libx264'\n", 8)
    assert other == "ffmpeg dừng (mã 8): Unknown encoder 'libx264'"
    assert describe_failure("", None) == "ffmpeg dừng."
