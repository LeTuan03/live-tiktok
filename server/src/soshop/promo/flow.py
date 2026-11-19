"""Google Flow, driven like a person would, in the user's own signed-in Chrome.

Flow (flow.google.com, November 2026) is agent-first: you attach media to a
prompt box, describe what you want, and Flow's agent picks the tool (here
reference-to-video), asks to approve the credit cost, and renders. This
module scripts exactly that, through the SoShop Flow Bridge extension:

1. open a dedicated background tab and a fresh project per video,
2. upload the host portrait and the product photo once,
3. per shot: new agent session, attach both references, type the prompt,
   check the quoted cost and approve it,
4. wait for the new video tile, then pull the MP4 with DevTools'
   ``Network.loadNetworkResource`` (the page's ``fetch`` is blocked by CORS
   on the googlevideo redirect).

Selectors lean on Flow's own custom elements (``flow-video-tile``,
``flow-permission-message``...) and Material icon ligatures, which do not
change with the UI language; visible text is only a fallback, matched in
Vietnamese and English. Every failure saves ``flow-error.png`` in the job
directory so a broken step can be seen.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from soshop.promo.bridge import BridgeError, FlowBridge
from soshop.promo.cdp import FLOW_HOME, FlowTab
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.promo.flow")

StageCallback = Callable[[Any, str], None]


class FlowModel(BaseModel):
    label: str
    """As named in Flow's model menu; the agent is asked for it by this name."""
    credits: int
    """What Flow quoted for one 8 s clip (checked 2026-11-15). Also the approval ceiling."""
    retiring: str | None = None


FLOW_MODELS = [
    FlowModel(label="Veo 3.1 - Lite", credits=10),
    FlowModel(label="Omni 1.2 Flash", credits=24),
    FlowModel(label="Veo 3.1 - Fast", credits=20, retiring="2026-12-05"),
    FlowModel(label="Veo 3.1 - Quality", credits=100, retiring="2026-12-05"),
]


def find_model(label: str) -> FlowModel | None:
    wanted = label.strip().lower()
    return next((m for m in FLOW_MODELS if m.label.lower() == wanted), None)


_START_TIMEOUT_S = 120.0
"""An approved generation shows its tile within seconds; this long means it never started."""

_COST_RE = re.compile(r"(\d+)\s*(?:tín dụng|credits?)", re.IGNORECASE)
_COUNT_RE = re.compile(r"(?:tạo|generate)\s+(\d+)\s+video", re.IGNORECASE)


class FlowError(RuntimeError):
    """A step in Flow failed; the message is meant for the studio user."""


def credit_ceiling(model_label: str) -> int:
    model = find_model(model_label)
    return model.credits if model else 30


def generation_request(prompt: str, model_label: str) -> str:
    """What is typed into Flow's prompt box for one shot."""
    return (
        f"Generate exactly one 8-second vertical 9:16 video with the {model_label} model as a "
        "reference-to-video generation. Reference images: the first attached image is the "
        "person (the host), the second attached image is the product. Use this prompt exactly "
        f"as written:\n{prompt}"
    )


def parse_permission(text: str) -> tuple[int | None, int | None]:
    """``(credits, videos)`` quoted in the agent's approval question."""
    cost = _COST_RE.search(text)
    count = _COUNT_RE.search(text)
    return (int(cost.group(1)) if cost else None, int(count.group(1)) if count else None)


# In-page helpers, injected once per page load. Finders locate Flow's
# controls; ``act`` and ``hover`` fire DOM events on them from inside the
# page. The job window sits behind the user's, and Chrome does not paint
# it: CDP mouse input is dropped there (no hit-test data) and screenshots
# hang on and off. DOM events need no paint. The one exception is the send
# button, which ignores synthetic clicks (see ``_trusted_click_until``).
_HELPERS = r"""
(() => {
  if (window.__soshop) return true;
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const iconButton = (root, icon) =>
    [...(root || document).querySelectorAll("button")].find(
      (b) => visible(b) && [...b.querySelectorAll("mat-icon")].some(
        (i) => i.textContent.trim() === icon));
  const byText = (selector, re) =>
    [...document.querySelectorAll(selector)].find((e) => visible(e) && re.test(e.innerText || ""));
  const promptBox = () => document.querySelector("flow-creative-agent-prompt-box");
  const promptEditor = () => {
    const box = promptBox();
    return box && (box.querySelector("[contenteditable=true]") ||
      box.querySelector("[role=textbox]"));
  };
  const lastPermission = () => {
    const msgs = [...document.querySelectorAll("flow-permission-message")];
    return msgs[msgs.length - 1] || null;
  };
  const videoKey = (tile) => {
    const v = tile.querySelector("video");
    const i = tile.querySelector("img");
    const src = (v && (v.currentSrc || v.getAttribute("src"))) || (i && i.src) || "";
    return src.split("=")[0];
  };
  const point = (el) => {
    const r = el.getBoundingClientRect();
    return { bubbles: true, cancelable: true, composed: true, view: window,
      clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, button: 0 };
  };
  const find = {
    newProjectButton: () => byText("button", /Dự án mới|New project/i),
    newSessionButton: () =>
      iconButton(document.querySelector("flow-agent-panel") || document, "edit_square"),
    addComponentButton: () => iconButton(promptBox(), "add"),
    uploadButton: () => iconButton(document, "upload") || byText("button", /Tải nội dung|Upload/i),
    assetItem: (name) => [...document.querySelectorAll("button.asset-item")].find(
      (b) => visible(b) && (b.innerText || "").includes(name)),
    submitButton: () => {
      const g = document.querySelector("flow-generate-icon-button button") ||
        iconButton(promptBox(), "arrow_forward");
      return g && !g.disabled ? g : null;
    },
    permissionOption: (index) => {
      const last = lastPermission();
      return (last && last.querySelectorAll("[role=radio]")[index]) || null;
    },
    newTile: () => document.querySelector("flow-video-tile[data-soshop-new]"),
  };
  window.__soshop = {
    rect(name, ...args) {
      const el = find[name](...args);
      if (!el) return null;
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    },
    exists(name, ...args) {
      return !!find[name](...args);
    },
    act(name, ...args) {
      const el = find[name](...args);
      if (!el) return false;
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const o = point(el);
      el.dispatchEvent(new PointerEvent("pointerdown", o));
      el.dispatchEvent(new MouseEvent("mousedown", o));
      if (el.focus) el.focus({ preventScroll: true });
      el.dispatchEvent(new PointerEvent("pointerup", o));
      el.dispatchEvent(new MouseEvent("mouseup", o));
      el.click();
      return true;
    },
    hover(name, ...args) {
      const el = find[name](...args);
      if (!el) return false;
      el.scrollIntoView({ block: "center", inline: "nearest" });
      for (const target of [el, el.firstElementChild, el.parentElement]) {
        if (!target) continue;
        const o = point(target);
        for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter",
                            "pointermove", "mousemove"]) {
          const E = type.startsWith("pointer") ? PointerEvent : MouseEvent;
          target.dispatchEvent(new E(type, o));
        }
      }
      return true;
    },
    closeOverlay() {
      const backdrop = document.querySelector(".cdk-overlay-backdrop");
      if (backdrop) { backdrop.click(); return true; }
      const target = document.activeElement || document.body;
      for (const type of ["keydown", "keyup"]) {
        target.dispatchEvent(new KeyboardEvent(type, { key: "Escape", code: "Escape",
          keyCode: 27, bubbles: true, cancelable: true }));
      }
      return true;
    },
    focusPrompt(clear) {
      const t = promptEditor();
      if (!t) return false;
      t.focus();
      const range = document.createRange();
      range.selectNodeContents(t);
      if (!clear) range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      if (clear && (t.innerText || "").trim()) document.execCommand("delete");
      return true;
    },
    insertPrompt(text) {
      return document.execCommand("insertText", false, text);
    },
    signedIn() {
      return !!document.querySelector('a[aria-label*="@"]');
    },
    onProject() {
      return location.pathname.startsWith("/project/") ? location.href : null;
    },
    pickerOpen() {
      // The picker panel carries an upload button; the "+" that opens it does not.
      return [...document.querySelectorAll("button.asset-item")].some(visible) ||
        !!iconButton(document, "upload");
    },
    pickerClosed() {
      return !window.__soshop.pickerOpen();
    },
    assetReady(name) {
      const b = find.assetItem(name);
      return !!b && !b.querySelector("mat-spinner,mat-progress-spinner,[role=progressbar]");
    },
    chipCount() {
      const box = promptBox();
      return box ? box.querySelectorAll("flow-image-ingredient-chip, img").length : 0;
    },
    chipsAbove(n) {
      return window.__soshop.chipCount() > n;
    },
    promptLength() {
      const t = promptEditor();
      return t ? (t.innerText || "").trim().length : 0;
    },
    promptEmpty() {
      return window.__soshop.promptLength() === 0;
    },
    chatEmpty() {
      return !document.querySelector("flow-agent-panel flow-chat-bubble, flow-permission-message");
    },
    pendingPermission() {
      const last = lastPermission();
      if (!last) return null;
      const options = [...last.querySelectorAll("[role=radio]")];
      if (!options.length || options.every((o) => o.getAttribute("aria-disabled") === "true"))
        return null;
      return { text: last.innerText.replace(/\s+/g, " ").trim(), count: options.length };
    },
    permissionChosen(index) {
      const o = find.permissionOption(index);
      return !!o && o.getAttribute("aria-checked") === "true";
    },
    lastAgentText() {
      const bubbles = [...document.querySelectorAll(
        "flow-agent-panel flow-markdown-content, flow-agent-panel flow-a2ui-text")];
      const last = bubbles[bubbles.length - 1];
      return last ? last.innerText.replace(/\s+/g, " ").trim().slice(0, 400) : "";
    },
    debugText() {
      const panel = document.querySelector("flow-agent-panel");
      const buttons = [...document.querySelectorAll("button")].filter(visible)
        .map((b) => (b.innerText || b.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim())
        .filter(Boolean).slice(0, 60);
      return [location.href, "--- agent panel ---", panel ? panel.innerText : "(none)",
              "--- buttons ---", ...buttons].join("\n");
    },
    markTiles() {
      for (const t of document.querySelectorAll("flow-video-tile")) t.dataset.soshopSeen = "1";
      return [...document.querySelectorAll("flow-video-tile")].map(videoKey).filter(Boolean);
    },
    tileState(seen) {
      const tiles = [...document.querySelectorAll("flow-video-tile")].filter(
        (t) => !t.dataset.soshopSeen && !seen.includes(videoKey(t)));
      const t = tiles[0];
      if (!t) return null;
      t.dataset.soshopNew = "1";
      const text = (t.innerText || "").replace(/\s+/g, " ").trim();
      const pct = (text.match(/(\d{1,3})%/) || [])[1];
      const icons = [...t.querySelectorAll("mat-icon")].map((i) => i.textContent.trim());
      const done = !pct && !!(t.querySelector("video[src]") ||
        [...t.querySelectorAll("img")].some((i) => /\/asb\//.test(i.src)));
      const failed = !pct && !done && icons.some((i) => /error|warning|report/.test(i));
      return { progress: pct ? Number(pct) : null, done, failed, text: text.slice(0, 200) };
    },
    newTileVideo() {
      const t = find.newTile();
      const v = t && t.querySelector("video");
      return v ? (v.currentSrc || v.getAttribute("src") || null) : null;
    },
  };
  return true;
})()
"""

# Flow builds its file input on demand and calls .click() on it. Capture the
# element instead of opening the OS dialog, then set files on it over CDP.
_CAPTURE_FILE_INPUT = r"""
(() => {
  if (!window.__soshopOrigClick) {
    window.__soshopOrigClick = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type === "file") { window.__soshopFileInput = this; return; }
      return window.__soshopOrigClick.call(this);
    };
  }
  window.__soshopFileInput = null;
  return true;
})()
"""


class FlowSession:
    """One Flow tab for one promo job."""

    def __init__(self, tab: FlowTab, debug_dir: Path) -> None:
        self.tab = tab
        self.debug_dir = debug_dir

    # ---------------------------------------------------------- lifecycle

    @classmethod
    async def open(cls, bridge: FlowBridge, debug_dir: Path) -> FlowSession:
        """Open Flow in a separate, unfocused window (a background tab with extension 0.1)."""
        try:
            opened = await bridge.call("windows.open", {"url": FLOW_HOME})
            tab_id = int(opened["tabId"])
        except BridgeError:
            opened = await bridge.call("tabs.open", {"url": FLOW_HOME, "active": False})
            tab_id = int(opened["id"])
        tab = FlowTab(bridge, tab_id)
        session = cls(tab, debug_dir)
        await tab.wait_ready(timeout=60.0)
        await session._settle()
        return session

    async def close(self) -> None:
        try:
            await self.tab.bridge.call("tabs.close", {"tabId": self.tab.tab_id})
            return
        except BridgeError:
            pass  # Extension 0.1.0 predates tabs.close; close the page over CDP.
        try:
            await self.tab.send("Page.close")
        except BridgeError:
            logger.info("flow tab already closed")

    async def _settle(self, seconds: float = 2.5) -> None:
        await asyncio.sleep(seconds)
        await self.tab.evaluate(_HELPERS)

    async def _fail(self, message: str) -> FlowError:
        """Record what the Flow tab showed, then build the error to raise."""
        try:
            text = await self._helper("debugText")
            (self.debug_dir / "flow-error.txt").write_text(f"{message}\n\n{text}", encoding="utf-8")
        except (BridgeError, OSError) as exc:
            logger.info("could not record the Flow page text: %s", exc)
        try:
            await self.tab.screenshot(self.debug_dir / "flow-error.png", timeout=10.0)
        except (BridgeError, KeyError, ValueError, TypeError) as exc:
            # Expected now and then: a never-painted background tab may not capture.
            logger.info("could not capture the Flow error screenshot: %s", exc)
        return FlowError(message)

    async def _helper(self, name: str, *args: Any) -> Any:
        payload = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        return await self.tab.evaluate(f"(window.__soshop || {{}}).{name}?.({payload}) ?? null")

    async def _click(self, name: str, *args: Any, what: str) -> None:
        if not await self._helper("act", name, *args):
            raise await self._fail(f"Không tìm thấy {what} trong Google Flow.")

    async def _click_until(
        self,
        name: str,
        *args: Any,
        done: str,
        done_args: tuple[Any, ...] = (),
        what: str,
        attempts: int = 4,
        wait: float = 8.0,
    ) -> Any:
        """Click, then poll helper ``done`` until truthy; click again if it stays falsy.

        Flow's Angular app can ignore a click that lands while it is still
        booting or re-rendering, so every state-changing click is checked.
        """
        for _attempt in range(attempts):
            if not await self._helper("exists", name, *args):
                # The control vanished: usually the last click worked and the UI moved on.
                result = await self._wait(done, *done_args, timeout=wait, interval=0.5)
                if result:
                    return result
                raise await self._fail(f"Không tìm thấy {what} trong Google Flow.")
            await self._helper("act", name, *args)
            result = await self._wait(done, *done_args, timeout=wait, interval=0.5)
            if result:
                return result
            await self.tab.evaluate(_HELPERS)
        raise await self._fail(f"Bấm {what} trong Google Flow không có tác dụng.")

    async def _trusted_click_until(
        self, name: str, *args: Any, done: str, what: str, attempts: int = 3
    ) -> Any:
        """A real click while the job window is in front, then hand focus back.

        Flow's send button ignores synthetic clicks, and Chrome delivers real
        input only to a painted tab, so the window is brought forward for the
        couple of seconds the click takes.
        """
        previous: int | None = None
        try:
            try:
                focused = await self.tab.bridge.call("windows.focus", {"tabId": self.tab.tab_id})
                previous = focused.get("previousWindowId")
            except BridgeError:
                await self.tab.bridge.call("tabs.activate", {"tabId": self.tab.tab_id})
            await asyncio.sleep(1.2)  # let Chrome paint the tab before clicking
            for _attempt in range(attempts):
                point = await self._helper("rect", name, *args)
                if not point:
                    result = await self._wait(done, timeout=8.0, interval=0.5)
                    if result:
                        return result
                    raise await self._fail(f"Không tìm thấy {what} trong Google Flow.")
                await self.tab.click_at(point["x"], point["y"])
                result = await self._wait(done, timeout=8.0, interval=0.5)
                if result:
                    return result
            raise await self._fail(f"Bấm {what} trong Google Flow không có tác dụng.")
        finally:
            if previous is not None:
                try:
                    await self.tab.bridge.call("windows.restore", {"windowId": previous})
                except BridgeError:
                    logger.info("could not hand focus back to window %s", previous)

    async def _wait(self, name: str, *args: Any, timeout: float, interval: float = 1.0) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            value = await self._helper(name, *args)
            if value:
                return value
            if loop.time() > deadline:
                return None
            await asyncio.sleep(interval)

    # ------------------------------------------------------------ project

    async def ensure_project(self, project_url: str | None) -> str:
        """Open ``project_url`` or create a new project; return its URL."""
        if project_url and project_url.startswith(f"{FLOW_HOME}project/"):
            await self.tab.navigate(project_url)
            await self._settle()
            url = await self._helper("onProject")
            if url:
                return str(url)
        if not await self._wait("signedIn", timeout=15.0):
            raise await self._fail("Chrome chưa đăng nhập Google Flow. Hãy đăng nhập rồi thử lại.")
        url = await self._click_until("newProjectButton", done="onProject", what="nút Dự án mới")
        await self._settle(3.0)
        return str(url)

    async def upload_references(self, paths: list[Path]) -> list[str]:
        """Upload reference images into the project. Returns the asset names."""
        names = [p.name for p in paths]
        await self.tab.evaluate(_CAPTURE_FILE_INPUT)
        await self._open_picker()
        if all([await self._wait("exists", "assetItem", n, timeout=3.0) for n in names]):
            # Reopened project (retry or one-shot regeneration): already uploaded.
            await self._close_picker()
            return names
        await self._click("uploadButton", what="nút tải nội dung lên")
        if not await self.tab.wait_for("!!window.__soshopFileInput", timeout=10.0, interval=0.5):
            raise await self._fail("Flow không mở ô chọn tệp để tải ảnh lên.")
        await self.tab.set_files("window.__soshopFileInput", paths)
        for name in names:
            if not await self._wait("assetReady", name, timeout=90.0, interval=1.5):
                raise await self._fail(f"Tải ảnh {name} lên Flow không xong.")
        await self._close_picker()
        return names

    async def _open_picker(self) -> None:
        """Open the prompt box's component picker unless it already is."""
        if await self._helper("pickerOpen"):
            return
        await self._click_until("addComponentButton", done="pickerOpen", what="nút thêm thành phần")

    async def _close_picker(self) -> None:
        if await self._helper("pickerOpen"):
            await self._helper("closeOverlay")
            await self._wait("pickerClosed", timeout=5.0, interval=0.5)

    # --------------------------------------------------------------- shot

    async def generate_clip(
        self,
        *,
        prompt: str,
        references: list[str],
        model_label: str,
        out: Path,
        on_stage: StageCallback,
    ) -> None:
        on_stage("submitting", "gửi yêu cầu cho Google Flow")
        await self._new_session()
        await self._attach(references)
        await self._type_prompt(generation_request(prompt, model_label))
        seen = await self._helper("markTiles") or []
        await self._trusted_click_until("submitButton", done="promptEmpty", what="nút gửi yêu cầu")
        await self._approve(model_label)
        on_stage("generating", "Flow đang tạo video")
        await self._wait_for_video(seen, on_stage)
        on_stage("downloading", "tải video về")
        await self._download(out)

    async def _new_session(self) -> None:
        """Start a fresh agent session so one shot's chat never steers the next."""
        if await self._helper("chatEmpty"):
            return
        await self._click_until("newSessionButton", done="chatEmpty", what="nút phiên mới")
        await self._settle(1.0)

    async def _attach(self, references: list[str]) -> None:
        for name in references:
            before = await self._helper("chipCount") or 0
            await self._open_picker()
            if not await self._wait("exists", "assetItem", name, timeout=15.0):
                raise await self._fail(f"Không thấy ảnh {name} trong thư viện của dự án Flow.")
            await self._click_until(
                "assetItem", name, done="chipsAbove", done_args=(before,), what=f"ảnh {name}"
            )
            await self._close_picker()

    async def _type_prompt(self, text: str) -> None:
        if not await self._helper("focusPrompt", True):
            raise await self._fail("Không tìm thấy ô nhập câu lệnh trong Google Flow.")
        await self.tab.insert_text(text)
        await asyncio.sleep(0.5)
        if (await self._helper("promptLength") or 0) < len(text) * 0.9:
            # CDP text input did not land (editor lost focus): type from inside the page.
            await self._helper("focusPrompt", True)
            await self._helper("insertPrompt", text)
            await asyncio.sleep(0.5)
        if (await self._helper("promptLength") or 0) < len(text) * 0.9:
            raise await self._fail("Không nhập được câu lệnh vào Google Flow.")

    async def _approve(self, model_label: str) -> None:
        """Approve the agent's cost question, after checking it is one clip within budget."""
        permission = await self._wait("pendingPermission", timeout=180.0, interval=1.5)
        if not permission:
            # "Never ask" mode generates straight away; anything else is a refusal
            # or a question the agent asked instead of generating.
            if await self._helper("tileState", []):
                return
            said = await self._helper("lastAgentText") or "không có phản hồi"
            raise await self._fail(f"Tác nhân Flow không bắt đầu tạo video. Flow trả lời: {said}")
        cost, count = parse_permission(permission["text"])
        ceiling = credit_ceiling(model_label)
        if (count is not None and count != 1) or (cost is not None and cost > ceiling):
            await self._click("permissionOption", permission["count"] - 1, what="nút Từ chối")
            raise await self._fail(
                f"Flow báo giá bất thường ({permission['text'][:160]}), đã từ chối để giữ tín dụng."
            )
        await self._click_until(
            "permissionOption", 0, done="permissionChosen", done_args=(0,), what="nút Phê duyệt"
        )
        logger.info("flow generation approved: %s credits", cost)

    async def _wait_for_video(self, seen: list[str], on_stage: StageCallback) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        appeared = False
        while loop.time() - started < settings.PROMO_SHOT_TIMEOUT_S:
            await self.tab.evaluate(_HELPERS)  # re-inject after an in-app navigation
            tile = await self._helper("tileState", seen)
            if tile:
                appeared = True
                if tile.get("done"):
                    return
                if tile.get("failed"):
                    raise await self._fail(f"Flow không tạo được video: {tile.get('text') or ''}")
                if tile.get("progress") is not None:
                    on_stage("generating", f"Flow đang tạo video {tile['progress']}%")
            elif not appeared and loop.time() - started > _START_TIMEOUT_S:
                said = await self._helper("lastAgentText") or ""
                raise await self._fail(f"Flow chưa bắt đầu tạo video sau 2 phút. {said}".strip())
            await asyncio.sleep(5.0)
        raise await self._fail("Flow tạo video quá lâu, đã dừng chờ.")

    async def _download(self, out: Path) -> None:
        src = None
        for _attempt in range(3):
            if not await self._helper("hover", "newTile"):
                raise await self._fail("Không tìm thấy video vừa tạo trong lưới của Flow.")
            src = await self._wait("newTileVideo", timeout=10.0, interval=0.5)
            if src:
                break
        if not src:
            raise await self._fail("Flow không trả về đường dẫn video.")
        data = await self._load_resource(str(src))
        if len(data) < 10_000 or data[4:8] != b"ftyp":
            raise await self._fail("Tệp video tải từ Flow không hợp lệ.")
        out.write_bytes(data)

    async def _load_resource(self, url: str) -> bytes:
        """Load ``url`` with the page's cookies, bypassing CORS, via DevTools."""
        frame = (await self.tab.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
        res = await self.tab.send(
            "Network.loadNetworkResource",
            {
                "frameId": frame,
                "url": url,
                "options": {"disableCache": False, "includeCredentials": True},
            },
            timeout=120.0,
        )
        resource = res.get("resource", {})
        stream = resource.get("stream")
        if not resource.get("success") or not stream:
            raise await self._fail(f"Không tải được video (HTTP {resource.get('httpStatusCode')}).")
        data = bytearray()
        try:
            while True:
                chunk = await self.tab.send("IO.read", {"handle": stream, "size": 1 << 20}, 60.0)
                raw = chunk.get("data", "")
                data += (
                    base64.b64decode(raw) if chunk.get("base64Encoded") else raw.encode("latin-1")
                )
                if chunk.get("eof"):
                    break
        finally:
            try:
                await self.tab.send("IO.close", {"handle": stream})
            except BridgeError:
                pass
        return bytes(data)
