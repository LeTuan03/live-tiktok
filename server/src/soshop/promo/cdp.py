"""A Google Flow tab driven over the bridge with Chrome DevTools Protocol.

Thin, generic helpers (evaluate, text input, file inputs, screenshots).
Everything Flow-specific lives in ``soshop.promo.flow``.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from soshop.promo.bridge import BridgeError, FlowBridge

FLOW_HOME = "https://flow.google.com/"


class FlowTab:
    def __init__(self, bridge: FlowBridge, tab_id: int) -> None:
        self.bridge = bridge
        self.tab_id = tab_id

    # ---------------------------------------------------------------- tabs

    async def navigate(self, url: str) -> None:
        await self.bridge.call("tabs.navigate", {"tabId": self.tab_id, "url": url})
        await asyncio.sleep(1.0)
        await self.wait_ready()

    async def send(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> Any:
        return await self.bridge.call(
            "cdp", {"tabId": self.tab_id, "method": method, "params": params or {}}, timeout
        )

    # ------------------------------------------------------------ evaluate

    async def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        """Evaluate JS in the page (promises awaited) and return its JSON value."""
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text")
            raise BridgeError(f"Lỗi JavaScript trong tab Flow: {text}")
        return result.get("result", {}).get("value")

    async def wait_ready(self, timeout: float = 30.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                if await self.evaluate("document.readyState") == "complete":
                    return
            except BridgeError:
                pass
            if asyncio.get_running_loop().time() > deadline:
                raise BridgeError("Tab Flow tải quá lâu.")
            await asyncio.sleep(0.5)

    async def wait_for(self, expression: str, timeout: float, interval: float = 1.0) -> Any:
        """Poll ``expression`` until it is truthy; return its value."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            value = await self.evaluate(expression)
            if value:
                return value
            if asyncio.get_running_loop().time() > deadline:
                return None
            await asyncio.sleep(interval)

    # --------------------------------------------------------------- input

    async def click_at(self, x: float, y: float) -> None:
        """A real (trusted) mouse click. Chrome only delivers it to a painted tab."""
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def insert_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": text})

    async def set_files(self, expression: str, paths: list[Path]) -> None:
        """Set files on the ``<input type=file>`` that ``expression`` evaluates to."""
        result = await self.send("Runtime.evaluate", {"expression": expression})
        object_id = result.get("result", {}).get("objectId")
        if not object_id:
            raise BridgeError("Không tìm thấy ô tải ảnh lên trong Flow.")
        await self.send(
            "DOM.setFileInputFiles",
            {"objectId": object_id, "files": [str(p.resolve()) for p in paths]},
        )

    # -------------------------------------------------------------- output

    async def screenshot(self, path: Path, timeout: float = 60.0) -> None:
        # Best effort: a never-painted background tab may not capture at all,
        # and an explicit viewport clip is what makes it work when it does.
        size = await self.evaluate("[innerWidth, innerHeight]")
        clip = {"x": 0, "y": 0, "width": size[0], "height": size[1], "scale": 1}
        result = await self.send(
            "Page.captureScreenshot", {"format": "png", "clip": clip}, timeout=timeout
        )
        path.write_bytes(base64.b64decode(result["data"]))
