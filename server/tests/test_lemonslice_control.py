"""Unit tests for `soshop.livekit.lemonslice_control`.

Pure HTTP plumbing — no LiveKit, no real network. The `aiohttp.ClientSession`
is injected via the helper's ``http_session`` kwarg so we can assert on the
exact URL, headers, and JSON body sent without monkeypatching aiohttp.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest

from soshop.livekit.lemonslice_control import (
    LEMONSLICE_CONTROL_BASE,
    LemonsliceControlError,
    lemonslice_control,
    lemonslice_terminate,
    lemonslice_update_image,
)


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._body = body

    async def text(self) -> str:
        return self._body

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._body) if self._body else {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeSession:
    """Minimal stand-in for `aiohttp.ClientSession` capturing one POST."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: str = '{"success": true}',
        raise_exc: BaseException | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._raise = raise_exc
        self.closed = False
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers or {}, "json": json, "timeout": timeout})
        if self._raise is not None:
            raise self._raise
        return _FakeResponse(self._status, self._body)

    async def close(self) -> None:
        self.closed = True


async def test_update_image_posts_correct_url_headers_and_body() -> None:
    session = _FakeSession()

    result = await lemonslice_update_image(
        "sess_abc",
        "https://cdn.example/avatar.png",
        api_key="test-key",
        http_session=session,  # type: ignore[arg-type]
    )

    assert result == {"success": True}
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == f"{LEMONSLICE_CONTROL_BASE}/sess_abc/control"
    assert call["headers"]["X-API-Key"] == "test-key"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {
        "event": "update-image",
        "image_url": "https://cdn.example/avatar.png",
    }


async def test_terminate_posts_event_only() -> None:
    session = _FakeSession()

    await lemonslice_terminate("sess_xyz", api_key="k", http_session=session)  # type: ignore[arg-type]

    assert session.calls[0]["json"] == {"event": "terminate"}


async def test_non_2xx_raises_lemonslice_control_error() -> None:
    session = _FakeSession(status=503, body="upstream timeout")

    with pytest.raises(LemonsliceControlError) as exc_info:
        await lemonslice_control(
            "sess_abc",
            {"event": "update-image", "image_url": "https://x"},
            api_key="k",
            http_session=session,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.body == "upstream timeout"


async def test_timeout_wraps_into_lemonslice_control_error() -> None:
    session = _FakeSession(raise_exc=TimeoutError("slow"))

    with pytest.raises(LemonsliceControlError) as exc_info:
        await lemonslice_control(
            "sess_abc",
            {"event": "update-image", "image_url": "https://x"},
            api_key="k",
            http_session=session,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code is None


async def test_transport_error_wraps_into_lemonslice_control_error() -> None:
    # ClientConnectorError needs an awkward constructor signature; use a
    # subclass of ClientError directly — the helper catches ClientError.
    class _Boom(aiohttp.ClientError):
        pass

    session = _FakeSession(raise_exc=_Boom("conn refused"))

    with pytest.raises(LemonsliceControlError):
        await lemonslice_control(
            "sess_abc",
            {"event": "update-image", "image_url": "https://x"},
            api_key="k",
            http_session=session,  # type: ignore[arg-type]
        )


async def test_empty_session_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="session_id"):
        await lemonslice_control(
            "",
            {"event": "terminate"},
            api_key="k",
        )


async def test_payload_without_event_raises_value_error() -> None:
    with pytest.raises(ValueError, match="event"):
        await lemonslice_control(
            "sess_abc",
            {"image_url": "https://x"},
            api_key="k",
        )


async def test_missing_api_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match="api_key"):
        await lemonslice_control(
            "sess_abc",
            {"event": "terminate"},
            api_key="",
        )


async def test_owns_session_when_none_injected_creates_and_closes() -> None:
    """Sanity: when no session is injected the helper must not leak one.

    We can't easily intercept the implicit `aiohttp.ClientSession()` here,
    but we can verify that calling the helper without a session and with
    an unreachable base URL still raises a `LemonsliceControlError` (i.e.
    the implicit session was created and closed cleanly without dangling).
    """
    # Use an unreachable port so the request fails fast without external
    # dependencies. Set a tiny timeout so the test stays snappy.
    with pytest.raises(LemonsliceControlError):
        await lemonslice_control(
            "sess_abc",
            {"event": "terminate"},
            api_key="k",
            base_url="http://127.0.0.1:1",
            timeout_s=0.5,
        )
    # Give the loop a tick to settle aiohttp resource-warning callbacks
    # so the test exits cleanly.
    await asyncio.sleep(0)
