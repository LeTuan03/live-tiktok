"""Server side of the SoShop Flow Bridge extension (``server/flow_extension``).

Google Flow has no public API, and its requests need the browser's own
sign-in and reCAPTCHA. The bridge extension runs in the user's everyday
Chrome, dials into ``/promo/flow/bridge`` and executes what we send it on
flow.google.com tabs: tab management plus raw Chrome DevTools Protocol
commands through ``chrome.debugger``.

One extension connection at a time; a new one replaces the old (the MV3
worker reconnects after it was put to sleep).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("soshop.promo.bridge")

_PING_EVERY_S = 20.0
"""Keeps the extension's MV3 service worker awake while connected."""

EventListener = Callable[[dict[str, Any]], None]


class BridgeError(RuntimeError):
    """The extension is not connected, or a command failed in the browser."""


class FlowBridge:
    def __init__(self) -> None:
        self._ws: WebSocket | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._listeners: list[EventListener] = []
        self.hello: dict[str, Any] | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def add_listener(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> Any:
        ws = self._ws
        if ws is None:
            raise BridgeError(
                "Chưa kết nối extension SoShop Flow Bridge. Mở Chrome có cài extension rồi thử lại."
            )
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await ws.send_json({"id": msg_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise BridgeError(
                f"Trình duyệt không phản hồi lệnh {method} sau {timeout:.0f} s."
            ) from exc
        except (RuntimeError, WebSocketDisconnect) as exc:
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError(f"Mất kết nối extension khi chạy {method}.") from exc
        finally:
            self._pending.pop(msg_id, None)

    def _dispatch(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        if isinstance(msg_id, int):
            future = self._pending.get(msg_id)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(BridgeError(str(message["error"])))
            else:
                future.set_result(message.get("result"))
            return
        event = message.get("event")
        if event == "hello":
            self.hello = message
            logger.info("flow bridge connected: extension %s", message.get("version"))
        for listener in list(self._listeners):
            try:
                listener(message)
            except Exception:
                logger.exception("flow bridge listener failed")

    async def _ping(self, ws: WebSocket) -> None:
        while True:
            await asyncio.sleep(_PING_EVERY_S)
            await ws.send_json({"id": 0, "method": "ping", "params": {}})

    async def serve(self, ws: WebSocket) -> None:
        """Own one extension connection until it closes."""
        await ws.accept()
        previous, self._ws = self._ws, ws
        if previous is not None:
            with contextlib.suppress(Exception):
                await previous.close()
        pinger = asyncio.create_task(self._ping(ws))
        try:
            while True:
                message = await ws.receive_json()
                if isinstance(message, dict):
                    self._dispatch(message)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            pinger.cancel()
            if self._ws is ws:
                self._ws = None
                self.hello = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(BridgeError("Extension đã ngắt kết nối."))
            logger.info("flow bridge disconnected")


bridge = FlowBridge()
