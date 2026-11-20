"""The media store: key rules, the local backend, ``/media`` serving, and S3.

The S3 test runs only when ``BUCKET_NAME`` (plus credentials) is set; the
rest is hermetic.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from soshop_server import storage
from soshop_server.api.routes.media import install_media
from soshop_server.core.config import settings


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "MEDIA_STORAGE", "local")
    return tmp_path


@pytest.mark.parametrize(
    "key",
    ["uploads/a.jpg", "promo/abc123/video-1a2b.mp4", "uploads/a_b-c.d.mp4"],
)
def test_valid_keys(key: str) -> None:
    assert storage.valid_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "uploads",
        "uploads/",
        "avatars/m1/status.json",
        "models/wav2lip.onnx",
        "work/promo/x/shot-1.mp4",
        "uploads/../.env",
        "uploads/.hidden",
        "uploads/a..b",
        r"uploads/..\secrets.txt",
        "/uploads/a.jpg",
        "uploads//a.jpg",
    ],
)
def test_invalid_keys(key: str) -> None:
    assert not storage.valid_key(key)


def test_local_store_round_trip(data_dir: Path, tmp_path: Path) -> None:
    store = storage.get_store()
    assert store.kind == "local"
    store.put_bytes("promo/j1/captions-a.vtt", b"WEBVTT", "text/vtt")
    assert (data_dir / "promo" / "j1" / "captions-a.vtt").read_bytes() == b"WEBVTT"
    assert store.get_bytes("promo/j1/captions-a.vtt") == b"WEBVTT"
    src = tmp_path / "src.mp4"
    src.write_bytes(b"mp4")
    store.put_file("promo/j1/video-a.mp4", src, "video/mp4")
    assert store.exists("promo/j1/video-a.mp4")
    assert store.url("promo/j1/video-a.mp4") == "/media/promo/j1/video-a.mp4"
    assert store.local_path("promo/j1/video-a.mp4") == data_dir / "promo" / "j1" / "video-a.mp4"
    copy = tmp_path / "copy.mp4"
    assert store.download("promo/j1/video-a.mp4", copy) and copy.read_bytes() == b"mp4"
    assert storage.local_file("promo/j1/video-a.mp4") == store.local_path("promo/j1/video-a.mp4")
    store.delete("promo/j1/captions-a.vtt")
    assert store.get_bytes("promo/j1/captions-a.vtt") is None
    store.delete_prefix("promo/j1")
    assert not (data_dir / "promo" / "j1").exists()
    assert not store.download("promo/j1/video-a.mp4", copy)
    with pytest.raises(storage.StorageError):
        store.put_bytes("avatars/m1/x.png", b"", "image/png")


def test_local_key_for_url(data_dir: Path) -> None:
    store = storage.get_store()
    assert store.key_for_url("/media/uploads/a.jpg") == "uploads/a.jpg"
    assert store.key_for_url("/media/uploads/a.jpg?v=3") == "uploads/a.jpg"
    assert store.key_for_url("/media/avatars/m1/clip.mp4") is None
    assert store.key_for_url("https://cdn.example/uploads/a.jpg") is None


def test_s3_urls_without_network() -> None:
    store = storage.S3Store(client=None, bucket="b", public_base="https://cdn.example/")
    assert store.url("uploads/a.jpg") == "https://cdn.example/uploads/a.jpg"
    assert store.key_for_url("https://cdn.example/uploads/a.jpg?x=1") == "uploads/a.jpg"
    # URLs stored while the app ran on local storage still resolve.
    assert store.key_for_url("/media/uploads/a.jpg") == "uploads/a.jpg"
    assert store.key_for_url("https://elsewhere.example/uploads/a.jpg") is None
    assert store.key_for_url("https://cdn.example/models/w.onnx") is None


def test_settings_refuse_s3_without_bucket() -> None:
    with pytest.raises(ValueError, match="MEDIA_STORAGE=s3"):
        type(settings)(MEDIA_STORAGE="s3", BUCKET_NAME=None, S3_PUBLIC_URL=None)


@pytest.fixture
async def media_client(data_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    install_media(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_media_route_serves_only_media_keys(
    media_client: httpx.AsyncClient, data_dir: Path
) -> None:
    (data_dir / "uploads").mkdir()
    (data_dir / "uploads" / "a.mp4").write_bytes(b"0123456789")
    (data_dir / "avatars" / "m1").mkdir(parents=True)
    (data_dir / "avatars" / "m1" / "status.json").write_text("{}", encoding="utf-8")
    (data_dir / ".env").write_text("SECRET=1", encoding="utf-8")

    ok = await media_client.get("/media/uploads/a.mp4")
    assert ok.status_code == 200 and ok.content == b"0123456789"
    assert ok.headers["cache-control"] == storage.CACHE_CONTROL
    ranged = await media_client.get("/media/uploads/a.mp4", headers={"Range": "bytes=2-4"})
    assert ranged.status_code == 206 and ranged.content == b"234"
    for path in ("/media/avatars/m1/status.json", "/media/.env", "/media/uploads/../.env"):
        assert (await media_client.get(path)).status_code == 404
    assert (await media_client.get("/media/uploads/missing.mp4")).status_code == 404


@pytest.mark.skipif(
    not (os.environ.get("BUCKET_NAME") and os.environ.get("S3_PUBLIC_URL")),
    reason="BUCKET_NAME / S3_PUBLIC_URL not set, skipping S3 media store test",
)
def test_s3_store_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_STORAGE", "s3")
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    store = storage.get_store()
    prefix = f"promo/pytest{uuid4().hex[:12]}"
    key = f"{prefix}/clip-a.txt"
    try:
        assert not store.exists(key)
        store.put_bytes(key, b"hello", "text/plain")
        assert store.exists(key) and store.get_bytes(key) == b"hello"
        cached = storage.local_file(key)
        assert cached is not None and cached.read_bytes() == b"hello"
        assert store.url(key).endswith(key)
    finally:
        store.delete_prefix(prefix)
    assert store.get_bytes(key) is None
