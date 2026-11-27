"""Where a broadcast can be pushed: RTMP ingest points on live platforms.

TikTok LIVE (LIVE Producer, LIVE Studio) and Shopee Live (Seller Centre) both
hand out a server URL and a stream key, usually a fresh pair for every live
session. :func:`ingest_url` joins the pair into the one URL ffmpeg pushes to.

Stream keys are secrets: anyone holding one can broadcast into the account.
Nothing leaves the API with a full key (:func:`mask_key`), and ffmpeg's error
text, which repeats the URL it failed on, goes through :func:`redact` before it
is stored or shown. No I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

Platform = Literal["tiktok", "shopee", "youtube", "custom"]
PLATFORMS: tuple[Platform, ...] = ("tiktok", "shopee", "youtube", "custom")

OutputState = Literal["off", "connecting", "live", "retrying", "error"]
"""How pushing one broadcast to one destination is going (``broadcast_outputs.state``)."""


@dataclass(frozen=True)
class OutputStatus:
    state: OutputState
    detail: str = ""


LABELS: dict[Platform, str] = {
    "tiktok": "TikTok Live",
    "shopee": "Shopee Live",
    "youtube": "YouTube Live",
    "custom": "RTMP khác",
}

_SCHEMES = ("rtmp", "rtmps")
_HIDDEN = "••••"


class DestinationError(ValueError):
    """A destination field that cannot be used as given (the message is user-facing)."""


def normalize_server_url(raw: str) -> str:
    """``raw`` trimmed, if it is an ``rtmp://`` or ``rtmps://`` URL with a host."""
    url = raw.strip()
    if not url:
        raise DestinationError("Hãy nhập Server URL.")
    if any(ch.isspace() for ch in url):
        raise DestinationError("Server URL không được chứa khoảng trắng.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in _SCHEMES:
        raise DestinationError("Server URL phải bắt đầu bằng rtmp:// hoặc rtmps://.")
    if not parts.hostname:
        raise DestinationError("Server URL thiếu tên máy chủ.")
    return url


def normalize_key(raw: str) -> str:
    """``raw`` trimmed; a stream key never contains whitespace."""
    key = raw.strip()
    if any(ch.isspace() for ch in key):
        raise DestinationError("Stream key không được chứa khoảng trắng.")
    return key


def needs_key(platform: Platform) -> bool:
    """A custom URL may carry its key inside; the platforms always give one apart."""
    return platform != "custom"


def ingest_url(server_url: str, stream_key: str) -> str:
    """The URL ffmpeg publishes to: the server URL, then ``/`` and the key."""
    if not stream_key:
        return server_url
    return f"{server_url.rstrip('/')}/{stream_key.lstrip('/')}"


def mask_key(key: str) -> str:
    """What the studio may show of a key: its last four characters, if it is long."""
    if not key:
        return ""
    return f"{_HIDDEN}{key[-4:]}" if len(key) >= 12 else _HIDDEN


def redact(text: str, *secrets: str) -> str:
    """``text`` with every occurrence of each secret replaced."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret and len(secret) >= 4:
            text = text.replace(secret, _HIDDEN)
    return text


_REFUSED = (
    "connection refused",
    "cannot open connection",
    "failed to resolve",
    "getaddrinfo",
    "no route to host",
    "network is unreachable",
    "timed out",
    "connection to tcp://",
    "error opening output",
    "error number -138",  # ETIMEDOUT as ffmpeg reports it on Windows
    "error number -10060",
    "error number -10061",
)
_REJECTED = (
    "input/output error",
    "server error",
    "error number -10053",
    "error number -10054",
    "connection reset",
    "broken pipe",
    "end of file",
)


def describe_failure(tail: str, returncode: int | None) -> str:
    """A short Vietnamese reason for a pusher that exited, from ffmpeg's last lines.

    ``tail`` must already be redacted. A server that accepted the connection
    and then dropped it usually means the stream key is wrong or has expired
    (TikTok and Shopee issue a new one for every live session); that is checked
    first, because ffmpeg reports it as a failure to open the output too. A
    connection that never got through points at the server URL or the network.
    """
    lowered = tail.lower()
    if any(sign in lowered for sign in _REJECTED):
        return (
            "Máy chủ ngắt luồng phát. Kiểm tra stream key còn hiệu lực "
            "(TikTok, Shopee cấp key mới cho mỗi phiên live)."
        )
    if any(sign in lowered for sign in _REFUSED):
        return "Không kết nối được máy chủ RTMP. Kiểm tra Server URL và kết nối mạng."
    last = next((line.strip() for line in reversed(tail.splitlines()) if line.strip()), "")
    code = f" (mã {returncode})" if returncode is not None else ""
    return f"ffmpeg dừng{code}: {last[:240]}" if last else f"ffmpeg dừng{code}."
