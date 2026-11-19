"""The TikTok cut: framing, captions, title, product and call-to-action.

Reads the rendered host back frame by frame and writes the 1080x1920 video:

* **shots**: a hard cut at every beat, alternating wide and punched-in
  framings with a slow push-in inside each shot, the way a talking-head
  TikTok is edited. Beats that carry an overlay stay wide so the overlay
  never covers the face;
* **captions**: two or three words at a time, the word being spoken in yellow,
  white type with a heavy outline, in the lower third;
* **hook title**: the script's on-screen title pops in over the first beat;
* **product**: on beats flagged ``show_product`` a full-screen cutaway of the
  product photo with a slow zoom (the voice continues), or a product card
  with name and price when there is no photo;
* **call-to-action**: price tag and buy button on the last beat, pulsing.

Overlays sit between the face and the captions (about 45 to 62 % of the
height): the top of a 9:16 portrait is the host's head, and TikTok's own UI
covers the bottom 15 %.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw, ImageFont

from soshop.advideo import media
from soshop.advideo.render import RenderedHost
from soshop.advideo.script import AdScript, ProductBrief
from soshop.advideo.voice import SpokenBeat, VoiceTrack, Word
from soshop.agent.lipsync.build import load_image
from soshop.agent.lipsync.performance import FaceAnchor
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.advideo")

OUT_W = 1080
OUT_H = 1920
_CRF = 18

_YELLOW = (77, 227, 255)  # BGR of #FFE34D
_WHITE = (255, 255, 255)
_RED = (92, 59, 255)  # BGR of #FF3B5C
_INK = (24, 24, 24)

_ZOOMS = (1.22, 1.0, 1.36, 1.08, 1.28)
_PUSH = 0.035
_FACE_Y_IN_SHOT = 0.36
_CAPTION_Y = 0.72
_OVERLAY_Y = 0.54
_CAPTION_WORDS = 3
_CAPTION_CHARS = 18
_POP_S = 0.18
_BROLL_MAX_S = 2.4

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguibl.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Black.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

ImageArray = npt.NDArray[np.uint8]
Progress = Callable[[float], None]


# ------------------------------------------------------------------ text


def font_path() -> str:
    """A bold font with Vietnamese glyphs: ``ADVIDEO_FONT`` or a system default."""
    if settings.ADVIDEO_FONT:
        return settings.ADVIDEO_FONT
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("no bold font found; set ADVIDEO_FONT to a .ttf with Vietnamese")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and font.getlength(lines[-1] + " " + word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def _to_bgra(image: Image.Image) -> ImageArray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])


def _rgb(bgr: tuple[int, int, int]) -> tuple[int, int, int]:
    return (bgr[2], bgr[1], bgr[0])


def caption_image(words: Sequence[str], active: int) -> ImageArray:
    """One caption chunk with word ``active`` highlighted, as BGRA."""
    font = _font(74)
    stroke = 8
    gap = font.getlength(" ")
    widths = [font.getlength(w) for w in words]
    width = int(sum(widths) + gap * (len(words) - 1)) + 2 * stroke + 8
    ascent, descent = font.getmetrics()
    height = ascent + descent + 2 * stroke + 8
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = stroke + 4.0
    for i, word in enumerate(words):
        fill = _rgb(_YELLOW) if i == active else _rgb(_WHITE)
        draw.text(
            (x, stroke + 4), word, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0)
        )
        x += widths[i] + gap
    return _to_bgra(image)


def _rounded_box(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    *,
    fill: tuple[int, int, int],
    ink: tuple[int, int, int],
    pad: int = 28,
    radius: int = 26,
) -> Image.Image:
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    width = int(max(font.getlength(line) for line in lines)) + 2 * pad
    height = line_h * len(lines) + 2 * pad - 8
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=_rgb(fill))
    for i, line in enumerate(lines):
        lw = font.getlength(line)
        draw.text(((width - lw) / 2, pad - 6 + i * line_h), line, font=font, fill=_rgb(ink))
    return image


def title_image(text: str) -> ImageArray:
    font = _font(70)
    return _to_bgra(_rounded_box(_wrap(text, font, int(OUT_W * 0.78)), font, fill=_WHITE, ink=_INK))


def screen_price(product: ProductBrief) -> str | None:
    """Price as shown on screen: ``199.000đ`` rather than the spoken form."""
    if product.price is None:
        return None
    if product.currency.upper() == "VND":
        return f"{round(float(product.price)):,}".replace(",", ".") + "đ"
    return product.price_text("en")


def product_card_image(product: ProductBrief) -> ImageArray:
    name_font, price_font = _font(56), _font(72)
    lines = _wrap(product.name, name_font, int(OUT_W * 0.7))[:2]
    name = _rounded_box(lines, name_font, fill=_WHITE, ink=_INK)
    price = screen_price(product)
    if price is None:
        return _to_bgra(name)
    tag = _rounded_box([price], price_font, fill=_RED, ink=_WHITE)
    width = max(name.width, tag.width)
    image = Image.new("RGBA", (width, name.height + tag.height + 14), (0, 0, 0, 0))
    image.alpha_composite(name, ((width - name.width) // 2, 0))
    image.alpha_composite(tag, ((width - tag.width) // 2, name.height + 14))
    return _to_bgra(image)


def cta_image(script: AdScript, product: ProductBrief) -> ImageArray:
    price = screen_price(product)
    button = _rounded_box([script.cta], _font(66), fill=_RED, ink=_WHITE, pad=30, radius=48)
    if price is None:
        return _to_bgra(button)
    tag = _rounded_box([price], _font(64), fill=_YELLOW, ink=_INK)
    width = max(button.width, tag.width)
    image = Image.new("RGBA", (width, tag.height + button.height + 16), (0, 0, 0, 0))
    image.alpha_composite(tag, ((width - tag.width) // 2, 0))
    image.alpha_composite(button, ((width - button.width) // 2, tag.height + 16))
    return _to_bgra(image)


# ---------------------------------------------------------------- layers


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def blend(canvas: ImageArray, layer: ImageArray, cx: float, cy: float, scale: float = 1.0) -> None:
    """Alpha-blend a BGRA ``layer`` centered at ``(cx, cy)``, clipped to the canvas."""
    if scale != 1.0:
        size = (max(1, round(layer.shape[1] * scale)), max(1, round(layer.shape[0] * scale)))
        layer = np.asarray(cv2.resize(layer, size, interpolation=cv2.INTER_LINEAR), dtype=np.uint8)
    h, w = layer.shape[:2]
    x0, y0 = round(cx - w / 2), round(cy - h / 2)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(canvas.shape[1], x0 + w), min(canvas.shape[0], y0 + h)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    part = layer[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0]
    alpha = part[:, :, 3:4].astype(np.float32) / 255.0
    region = canvas[cy0:cy1, cx0:cx1]
    region[:] = (part[:, :, :3] * alpha + region * (1.0 - alpha)).astype(np.uint8)


@dataclass(frozen=True)
class Shot:
    start: float
    end: float
    zoom: float


def plan_shots(track: VoiceTrack, script: AdScript, has_photo: bool) -> list[Shot]:
    """One shot per beat; beats with an overlay stay wide."""
    shots: list[Shot] = []
    cycle = 0
    last = len(track.beats) - 1
    for i, spoken in enumerate(track.beats):
        start = 0.0 if i == 0 else spoken.start
        end = track.duration if i == last else track.beats[i + 1].start
        beat = script.beats[spoken.index]
        overlay = i in (0, last) or (beat.show_product and not has_photo)
        if overlay:
            zoom = 1.0
        else:
            zoom = _ZOOMS[cycle % len(_ZOOMS)]
            cycle += 1
        shots.append(Shot(start, end, zoom))
    return shots


def frame_shot(frame: ImageArray, shot: Shot, seconds: float, face: FaceAnchor) -> ImageArray:
    """Crop the host frame for ``shot`` and scale it to the output size."""
    src_h, src_w = frame.shape[:2]
    progress = (seconds - shot.start) / max(shot.end - shot.start, 1e-6)
    zoom = shot.zoom + _PUSH * min(max(progress, 0.0), 1.0)
    w, h = src_w / zoom, src_h / zoom
    cx = min(max(face.cx, w / 2), src_w - w / 2)
    cy = face.cy + h * (0.5 - _FACE_Y_IN_SHOT)
    cy = min(max(cy, h / 2), src_h - h / 2)
    matrix = np.array(
        [[OUT_W / w, 0, -(cx - w / 2) * OUT_W / w], [0, OUT_H / h, -(cy - h / 2) * OUT_H / h]],
        dtype=np.float32,
    )
    out = cv2.warpAffine(
        frame, matrix, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    blurred = cv2.GaussianBlur(out, (0, 0), 1.2)
    return np.asarray(cv2.addWeighted(out, 1.35, blurred, -0.35, 0), dtype=np.uint8)


def broll_frames(photo: ImageArray) -> Callable[[float], ImageArray]:
    """A full-screen product shot: blurred fill behind the photo, slow zoom."""
    scale = max(OUT_W / photo.shape[1], OUT_H / photo.shape[0])
    back = cv2.resize(photo, (math.ceil(photo.shape[1] * scale), math.ceil(photo.shape[0] * scale)))
    y0, x0 = (back.shape[0] - OUT_H) // 2, (back.shape[1] - OUT_W) // 2
    back = cv2.GaussianBlur(back[y0 : y0 + OUT_H, x0 : x0 + OUT_W], (0, 0), 28)
    back = np.asarray(cv2.convertScaleAbs(back, alpha=0.6), dtype=np.uint8)
    fit = min(OUT_W * 0.9 / photo.shape[1], OUT_H * 0.62 / photo.shape[0])
    front = cv2.resize(
        photo,
        (round(photo.shape[1] * fit), round(photo.shape[0] * fit)),
        interpolation=cv2.INTER_AREA,
    )
    radius = 36
    mask = np.zeros(front.shape[:2], dtype=np.uint8)
    cv2.rectangle(mask, (radius, 0), (front.shape[1] - radius, front.shape[0]), 255, -1)
    cv2.rectangle(mask, (0, radius), (front.shape[1], front.shape[0] - radius), 255, -1)
    for cx in (radius, front.shape[1] - radius):
        for cy in (radius, front.shape[0] - radius):
            cv2.circle(mask, (cx, cy), radius, 255, -1, lineType=cv2.LINE_AA)
    card: ImageArray = np.dstack([front, mask]).astype(np.uint8)

    def at(progress: float) -> ImageArray:
        canvas = np.array(back, dtype=np.uint8)
        blend(canvas, card, OUT_W / 2, OUT_H * 0.42, 1.0 + 0.06 * progress)
        return canvas

    return at


# ----------------------------------------------------------------- cut


@dataclass(frozen=True)
class _Chunk:
    words: tuple[Word, ...]
    start: float
    end: float


def caption_chunks(track: VoiceTrack) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    for spoken in track.beats:
        group: list[Word] = []
        for word in spoken.words:
            text = " ".join(w.text for w in [*group, word])
            if group and (len(group) >= _CAPTION_WORDS or len(text) > _CAPTION_CHARS):
                chunks.append(_Chunk(tuple(group), group[0].start, group[-1].end))
                group = []
            group.append(word)
            if word.text[-1:] in ",.!?" and len(group) >= 2:
                chunks.append(_Chunk(tuple(group), group[0].start, group[-1].end))
                group = []
        if group:
            chunks.append(_Chunk(tuple(group), group[0].start, group[-1].end))
    return chunks


class Cut:
    """Everything drawn over the host, looked up by time."""

    def __init__(self, track: VoiceTrack, script: AdScript, product: ProductBrief) -> None:
        self.track = track
        self.script = script
        self.product = product
        self.chunks = caption_chunks(track)
        self.title = title_image(script.hook_title) if script.hook_title else None
        self.card = product_card_image(product)
        self.cta = cta_image(script, product)
        self.photo: Callable[[float], ImageArray] | None = None
        if product.image:
            try:
                self.photo = broll_frames(load_image(product.image))
            except Exception:
                logger.warning("product photo unreadable; using the product card", exc_info=True)
        self.shots = plan_shots(track, script, self.photo is not None)
        self._captions: dict[tuple[int, int], ImageArray] = {}

    def shot_at(self, seconds: float) -> Shot:
        for shot in reversed(self.shots):
            if seconds >= shot.start:
                return shot
        return self.shots[0]

    def _beat(self, seconds: float) -> tuple[int, SpokenBeat] | None:
        for i, spoken in enumerate(self.track.beats):
            start = 0.0 if i == 0 else spoken.start
            end = self.track.duration if i == len(self.track.beats) - 1 else spoken.end
            if start <= seconds < end:
                return i, spoken
        return None

    def broll(self, seconds: float) -> ImageArray | None:
        found = self._beat(seconds)
        if self.photo is None or found is None:
            return None
        i, spoken = found
        if not self.script.beats[spoken.index].show_product or i in (0, len(self.track.beats) - 1):
            return None
        length = min(_BROLL_MAX_S, spoken.end - spoken.start)
        local = seconds - spoken.start
        if not 0.0 <= local < length:
            return None
        return self.photo(local / length)

    def caption(self, seconds: float) -> ImageArray | None:
        for index, chunk in enumerate(self.chunks):
            if chunk.start <= seconds < chunk.end + 0.12:
                active = max(
                    (k for k, w in enumerate(chunk.words) if w.start <= seconds), default=0
                )
                key = (index, active)
                if key not in self._captions:
                    self._captions[key] = caption_image([w.text for w in chunk.words], active)
                return self._captions[key]
        return None

    def overlays(self, seconds: float) -> list[tuple[ImageArray, float, float, float]]:
        """(layer, center x, center y, scale) drawn above the picture, below captions."""
        found = self._beat(seconds)
        if found is None:
            return []
        i, spoken = found
        local = seconds - (0.0 if i == 0 else spoken.start)
        pop = 0.85 + 0.15 * _ease(local / _POP_S)
        out: list[tuple[ImageArray, float, float, float]] = []
        if i == 0 and self.title is not None:
            out.append((self.title, OUT_W / 2, OUT_H * _OVERLAY_Y, pop))
        elif i == len(self.track.beats) - 1:
            pulse = 1.0 + 0.035 * math.sin(2 * math.pi * 1.6 * local)
            out.append((self.cta, OUT_W / 2, OUT_H * _OVERLAY_Y, pop * pulse))
        elif self.script.beats[spoken.index].show_product and self.photo is None:
            out.append((self.card, OUT_W / 2, OUT_H * _OVERLAY_Y, pop))
        return out

    def draw(self, frame: ImageArray, seconds: float, face: FaceAnchor) -> ImageArray:
        canvas = self.broll(seconds)
        if canvas is None:
            canvas = frame_shot(frame, self.shot_at(seconds), seconds, face)
        for layer, cx, cy, scale in self.overlays(seconds):
            blend(canvas, layer, cx, cy, scale)
        caption = self.caption(seconds)
        if caption is not None:
            blend(canvas, caption, OUT_W / 2, OUT_H * _CAPTION_Y)
        return canvas


def _encoder_args(out_path: Path, voice: Path, fps: int, music: Path | None) -> list[str]:
    args = [*media.raw_video_input(OUT_W, OUT_H, fps), "-i", str(voice)]
    if music is not None:
        args += [
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-filter_complex",
            "[2:a]volume=0.12[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
        ]
    return [
        *args,
        *media.h264(_CRF),
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def compose(
    host: RenderedHost,
    track: VoiceTrack,
    script: AdScript,
    product: ProductBrief,
    out_path: Path,
    *,
    music: Path | None = None,
    thumbnail: Path | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write the finished video to ``out_path``."""
    cut = Cut(track, script, product)
    voice_wav = out_path.with_suffix(".voice.wav")
    track.write_wav(voice_wav)
    decoder, source = media.reader(
        ["-i", str(host.path), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    )
    encoder, sink = media.writer(_encoder_args(out_path, voice_wav, host.fps, music))
    size = host.width * host.height * 3
    try:
        for index in range(host.frames):
            raw = source.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(host.height, host.width, 3)
            seconds = index / host.fps
            canvas = cut.draw(frame.copy(), seconds, host.face)
            sink.write(canvas.tobytes())
            if thumbnail is not None and index == round(host.fps * 1.2):
                cv2.imwrite(str(thumbnail), canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if progress is not None and index % 10 == 0:
                progress((index + 1) / host.frames)
    finally:
        sink.close()
        code = encoder.wait()
        decoder.kill()
        decoder.wait()
        voice_wav.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed to write the video (exit {code})")
    return out_path
