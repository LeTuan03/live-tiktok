"""Lớp điều khiển trang flow.google.com nhúng trong app.

Flow **không có API công khai**, nên LiveFlow điều khiển chính giao diện Flow bên trong
một trình duyệt nhúng (QtWebEngine): tự dán prompt, tự bấm tạo, tự bắt video mới.

Mọi thứ ở đây đều **tự dò theo đặc điểm phần tử** (placeholder, aria-label, kích thước)
thay vì bám một chuỗi CSS cố định, vì Google đổi giao diện Flow khá thường xuyên.
Khi Google đổi nhiều quá, ghi đè bằng file ``flow_selectors.json`` trong thư mục dữ liệu:

    {
      "promptSelector":   "textarea#prompt",
      "generateSelector": "button[aria-label='Generate']",
      "videoSelector":    "video",
      "downloadSelector": "button[aria-label='Download']"
    }

Không cần sửa code, chỉ cần bấm "Nạp lại bộ chọn" trong Flow Studio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

SELECTOR_FILE = "flow_selectors.json"

# User-Agent của Chrome thật — Google chặn đăng nhập trên trình duyệt nhúng lạ.
CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

DEFAULT_SELECTORS: Dict[str, str] = {
    "promptSelector": "",
    "generateSelector": "",
    "videoSelector": "video",
    "downloadSelector": "",
}


def load_selectors(path: Optional[Path | str] = None) -> Dict[str, str]:
    """Đọc bộ chọn ghi đè (nếu có)."""
    if path is None:
        try:
            from app.config.settings import get_settings

            path = get_settings().data_dir / SELECTOR_FILE
        except Exception:
            path = Path.home() / ".liveflow" / SELECTOR_FILE
    p = Path(path)
    data = dict(DEFAULT_SELECTORS)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            for k in DEFAULT_SELECTORS:
                if isinstance(raw.get(k), str):
                    data[k] = raw[k]
            logger.info("Đã nạp bộ chọn Flow ghi đè từ {}", p)
        except Exception as e:
            logger.warning("Không đọc được {}: {}", p, e)
    return data


# ---------------------------------------------------------------------------
# Thư viện JS tiêm vào trang Flow
# ---------------------------------------------------------------------------
_JS_LIBRARY = r"""
(function () {
  if (window.__LIVEFLOW__ && window.__LIVEFLOW__.version === 3) { return "already"; }

  var CFG = __CONFIG__;

  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    var st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || parseFloat(st.opacity) < 0.05) return false;
    return true;
  }

  function textOf(el) {
    var bits = [
      el.getAttribute && el.getAttribute("aria-label"),
      el.getAttribute && el.getAttribute("placeholder"),
      el.getAttribute && el.getAttribute("title"),
      el.getAttribute && el.getAttribute("data-placeholder"),
      el.innerText
    ];
    return bits.filter(Boolean).join(" ").toLowerCase().slice(0, 300);
  }

  // ---------------------------------------------------------------- prompt box
  var PROMPT_HINT = /(prompt|describe|idea|generat|create|video|scene|mô tả|nhập|tạo)/i;

  function findPromptBox() {
    if (CFG.promptSelector) {
      var forced = document.querySelector(CFG.promptSelector);
      if (forced) return forced;
    }
    var nodes = Array.prototype.slice.call(
      document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"], input[type="text"]')
    );
    var best = null, bestScore = -1;
    nodes.forEach(function (el) {
      if (!visible(el) || el.disabled || el.readOnly) return;
      var r = el.getBoundingClientRect();
      var score = Math.min(r.width, 1200) / 10;          // ô càng rộng càng có thể là ô prompt
      if (PROMPT_HINT.test(textOf(el))) score += 220;
      if (el.tagName === "TEXTAREA") score += 90;
      if (el.getAttribute("contenteditable") === "true") score += 60;
      if (r.top > window.innerHeight * 0.45) score += 50; // ô prompt của Flow nằm phía dưới
      if (el.closest("form")) score += 20;
      if (score > bestScore) { bestScore = score; best = el; }
    });
    return best;
  }

  function setNativeValue(el, value) {
    var proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) { setter.set.call(el, value); } else { el.value = value; }
  }

  function setPrompt(text) {
    var el = findPromptBox();
    if (!el) return { ok: false, error: "khong_thay_o_prompt" };
    el.scrollIntoView({ block: "center" });
    el.focus();
    try {
      if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
        setNativeValue(el, "");
        el.dispatchEvent(new Event("input", { bubbles: true }));
        setNativeValue(el, text);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        // Trình soạn thảo giàu (Lexical/ProseMirror): execCommand là cách được hỗ trợ rộng nhất
        document.execCommand("selectAll", false, null);
        document.execCommand("delete", false, null);
        var inserted = document.execCommand("insertText", false, text);
        if (!inserted) { el.textContent = text; }
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
      }
    } catch (e) {
      return { ok: false, error: String(e) };
    }
    var now = (el.value !== undefined && el.value !== null && el.value !== "")
      ? el.value : (el.innerText || el.textContent || "");
    return { ok: now.trim().length > 0, length: now.length, tag: el.tagName };
  }

  // ------------------------------------------------------------ generate button
  var GEN_TEXT = /^(generate|create|tạo|tao video|generate video|run|submit|send)$/i;
  var GEN_HINT = /(generate|create video|tạo video|submit|send|arrow_upward|arrow_forward)/i;

  function findGenerateButton() {
    if (CFG.generateSelector) {
      var forced = document.querySelector(CFG.generateSelector);
      if (forced) return forced;
    }
    var box = findPromptBox();
    var boxRect = box ? box.getBoundingClientRect() : null;
    var nodes = Array.prototype.slice.call(
      document.querySelectorAll('button, [role="button"], [type="submit"]')
    );
    var best = null, bestScore = -1;
    nodes.forEach(function (el) {
      if (!visible(el) || el.disabled || el.getAttribute("aria-disabled") === "true") return;
      var t = textOf(el).trim();
      var score = 0;
      if (GEN_TEXT.test((el.innerText || "").trim())) score += 300;
      if (GEN_HINT.test(t)) score += 200;
      if (el.type === "submit") score += 60;
      if (box && el.closest("form") && box.closest("form") === el.closest("form")) score += 120;
      if (boxRect) {
        var r = el.getBoundingClientRect();
        var dy = Math.abs(r.top - boxRect.top);
        if (dy < 160) score += 120 - dy / 2;            // nút nằm ngay cạnh ô prompt
        if (r.left > boxRect.left) score += 30;         // thường ở mép phải ô prompt
      }
      if (score > bestScore) { bestScore = score; best = el; }
    });
    return bestScore >= 100 ? best : null;
  }

  function clickGenerate() {
    // Khong co o prompt = dang o trang gioi thieu /about hoac chua mo project.
    // Bam bua vao nut "Create" o day chi lam dieu huong, mat trang dang mo.
    if (!findPromptBox()) {
      return { ok: false, error: "chua_mo_trinh_soan_thao" };
    }
    var btn = findGenerateButton();
    if (!btn) {
      // Phương án 2: Flow nhận Enter/Ctrl+Enter trong ô prompt
      var box = findPromptBox();
      if (!box) return { ok: false, error: "khong_thay_nut_tao" };
      box.focus();
      ["keydown", "keypress", "keyup"].forEach(function (type) {
        box.dispatchEvent(new KeyboardEvent(type, {
          key: "Enter", code: "Enter", keyCode: 13, which: 13,
          bubbles: true, cancelable: true, ctrlKey: true
        }));
      });
      return { ok: true, via: "enter" };
    }
    btn.scrollIntoView({ block: "center" });
    btn.click();
    return { ok: true, via: "click", label: textOf(btn).slice(0, 80) };
  }

  // -------------------------------------------------------------------- videos
  function videoNodes() {
    return Array.prototype.slice.call(document.querySelectorAll(CFG.videoSelector || "video"));
  }

  function srcOf(v) {
    var s = v.currentSrc || v.src || "";
    if (!s) {
      var child = v.querySelector("source");
      if (child) s = child.src || child.getAttribute("src") || "";
    }
    return s;
  }

  function videoSrcs() {
    var out = [];
    videoNodes().forEach(function (v) {
      var s = srcOf(v);
      if (s) out.push(s);
    });
    return out;
  }

  var BUSY_HINT = /(generating|creating|rendering|processing|đang tạo|đang xử lý)/i;

  function isBusy() {
    if (document.querySelector('[aria-busy="true"], progress, [role="progressbar"]')) return true;
    var body = (document.body.innerText || "").slice(0, 4000);
    return BUSY_HINT.test(body);
  }

  function state() {
    var box = findPromptBox();
    var btn = box ? findGenerateButton() : null;
    var href = location.href;
    var onSignIn = /accounts\.google\.com/.test(href);
    // flow.google.com/about la trang gioi thieu cho nguoi CHUA dang nhap
    var onAbout = /flow\.google\.com\/about/.test(href);
    var hasSignInCta = /(sign in|dang nhap|đăng nhập)/i.test(
      (document.body ? document.body.innerText : "").slice(0, 3000)
    );
    return {
      url: href,
      signedIn: !onSignIn && !onAbout,
      onAbout: onAbout,
      onSignIn: onSignIn,
      hasSignInCta: hasSignInCta,
      promptFound: !!box,
      generateFound: !!btn,
      generateLabel: btn ? textOf(btn).slice(0, 60) : "",
      busy: isBusy(),
      videos: videoSrcs()
    };
  }

  // ------------------------------------------------------------------ download
  function triggerDownload(url, filename) {
    var a = document.createElement("a");
    a.href = url;
    a.download = filename || "liveflow_flow_clip.mp4";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { a.remove(); }, 4000);
  }

  // Tải một nguồn video về máy.
  // Link video của Flow nằm khác tên miền, mà thuộc tính `download` bị bỏ qua với link
  // khác tên miền (bấm vào là ĐIỀU HƯỚNG, làm mất trang Flow đang mở). Vì vậy: tải bằng
  // fetch -> blob rồi mới bấm; blob là cùng gốc nên `download` có hiệu lực.
  function downloadSrc(url, filename) {
    if (!url) return { ok: false, error: "thieu_url" };
    if (/^(blob:|data:)/.test(url) || url.indexOf(location.origin) === 0) {
      triggerDownload(url, filename);
      window.__LF_DL__ = { state: "ok", via: "direct" };
      return { ok: true, via: "direct" };
    }
    window.__LF_DL__ = { state: "pending", via: "fetch" };
    try {
      fetch(url, { credentials: "include", mode: "cors" })
        .then(function (r) {
          if (!r.ok) throw new Error("http " + r.status);
          return r.blob();
        })
        .then(function (b) {
          var u = URL.createObjectURL(b);
          triggerDownload(u, filename);
          setTimeout(function () { URL.revokeObjectURL(u); }, 120000);
          window.__LF_DL__ = { state: "ok", via: "fetch", size: b.size };
        })
        .catch(function (e) {
          // Thường là bị CORS chặn -> Python sẽ bấm nút Download của Flow thay thế.
          window.__LF_DL__ = { state: "err", error: String(e) };
        });
      return { ok: true, via: "fetch" };
    } catch (e) {
      window.__LF_DL__ = { state: "err", error: String(e) };
      return { ok: false, error: String(e) };
    }
  }

  function dlState() { return window.__LF_DL__ || { state: "none" }; }

  // Tải video mới (src chưa có trong `known`).
  function downloadNew(known, filename) {
    var current = videoSrcs();
    var fresh = current.filter(function (s) { return known.indexOf(s) < 0; });
    var target = fresh.length ? fresh[fresh.length - 1] : null;
    if (!target) return { ok: false, error: "chua_co_video_moi", videos: current };
    var r = downloadSrc(target, filename);
    r.src = target;
    r.videos = current;
    return r;
  }

  // Bấm đúng nút Download của Flow (dự phòng khi lấy src trực tiếp không được).
  function clickDownload() {
    var sel = CFG.downloadSelector;
    var btn = sel ? document.querySelector(sel) : null;
    if (!btn) {
      var nodes = Array.prototype.slice.call(document.querySelectorAll('button, [role="button"], a'));
      var hits = nodes.filter(function (el) {
        return visible(el) && /(download|tải xuống|tải về|export)/i.test(textOf(el));
      });
      btn = hits.length ? hits[hits.length - 1] : null;
    }
    if (!btn) return { ok: false, error: "khong_thay_nut_tai" };
    btn.scrollIntoView({ block: "center" });
    btn.click();
    return { ok: true };
  }

  window.__LIVEFLOW__ = {
    version: 3,
    cfg: CFG,
    setPrompt: setPrompt,
    clickGenerate: clickGenerate,
    state: state,
    videoSrcs: videoSrcs,
    downloadSrc: downloadSrc,
    downloadNew: downloadNew,
    dlState: dlState,
    clickDownload: clickDownload,
    isBusy: isBusy
  };
  return "ok";
})();
"""


def library_js(selectors: Optional[Dict[str, str]] = None) -> str:
    """Trả về mã JS cài thư viện điều khiển vào trang."""
    cfg = json.dumps(selectors or load_selectors(), ensure_ascii=False)
    return _JS_LIBRARY.replace("__CONFIG__", cfg)


def _wrap(expr: str) -> str:
    """Bọc một biểu thức JS thành chuỗi JSON.

    QtWebEngine KHÔNG chuyển được object JS trả về thành dict Python (nhận lại chuỗi rỗng),
    nên mọi lệnh đều trả JSON rồi Python tự parse bằng ``parse_result``.
    """
    return (
        "(function(){try{"
        "if(!window.__LIVEFLOW__){return JSON.stringify({ok:false,error:'chua_cai_thu_vien'});}"
        f"var r={expr};"
        "return JSON.stringify(r===undefined?null:r);"
        "}catch(e){return JSON.stringify({ok:false,error:String(e)});}})();"
    )


def parse_result(value: Any) -> Any:
    """Đọc kết quả runJavaScript (chuỗi JSON) thành dict/list Python."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def js_set_prompt(prompt: str) -> str:
    return _wrap(f"window.__LIVEFLOW__.setPrompt({json.dumps(prompt)})")


def js_click_generate() -> str:
    return _wrap("window.__LIVEFLOW__.clickGenerate()")


def js_state() -> str:
    return _wrap("window.__LIVEFLOW__.state()")


def js_video_srcs() -> str:
    return _wrap("window.__LIVEFLOW__.videoSrcs()")


def js_download_new(known: Any, filename: str) -> str:
    return _wrap(
        "window.__LIVEFLOW__.downloadNew("
        f"{json.dumps(list(known or []))}, {json.dumps(filename)})"
    )


def js_download_src(src: str, filename: str) -> str:
    return _wrap(
        f"window.__LIVEFLOW__.downloadSrc({json.dumps(src)}, {json.dumps(filename)})"
    )


def js_dl_state() -> str:
    return _wrap("window.__LIVEFLOW__.dlState()")


def js_click_download() -> str:
    return "window.__LIVEFLOW__ && window.__LIVEFLOW__.clickDownload();"
