"""Stitch Flow's shot clips into the final promo video with ffmpeg.

Every clip is normalised to the same 720x1280, 30 fps, AAC stereo shape (a
clip without audio gets silence), a short end card with the product photo,
name and price is appended, and the parts are joined. Captions go into a
WebVTT sidecar the web player can toggle, rather than being burned in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from soshop.promo.script import PromoProduct
from soshop_server.core.config import settings

WIDTH, HEIGHT, FPS = 720, 1280, 30
END_CARD_SECONDS = 2.5
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguibl.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


class AssembleError(RuntimeError):
    """ffmpeg is missing or failed."""


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise AssembleError("Chưa cài ffmpeg trên server (cần có trong PATH).")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ffmpeg(), "-hide_banner", *args], capture_output=True, text=True, check=False
    )
    return proc


def probe(path: Path) -> tuple[float, bool]:
    """``(duration_s, has_audio)`` read from ffmpeg's banner."""
    err = _run(["-i", str(path)]).stderr
    match = _DURATION_RE.search(err)
    duration = (
        int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        if match
        else 0.0
    )
    return duration, "Audio:" in err


def _check(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise AssembleError(f"ffmpeg không {what} được: {tail}")


_ENCODE = [
    "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
    "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
]  # fmt: skip
_FIT = (
    f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
    f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}"
)


def normalise(src: Path, dst: Path) -> float:
    """Re-encode one clip to the common shape. Returns its duration."""
    duration, has_audio = probe(src)
    args = ["-y", "-i", str(src)]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-vf", _FIT, "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0"]
    args += ["-shortest", *_ENCODE, "-movflags", "+faststart", str(dst)]
    _check(_run(args), "chuẩn hóa clip")
    return duration or probe(dst)[0]


# ----------------------------------------------------------------- end card


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (settings.ADVIDEO_FONT, *_FONT_CANDIDATES):
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: object, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:  # type: ignore[arg-type]
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def screen_price(product: PromoProduct) -> str | None:
    if product.price is None:
        return None
    if product.currency.upper() == "VND":
        return f"{round(product.price):,}".replace(",", ".") + "đ"
    amount = f"{product.price:,.2f}".rstrip("0").rstrip(".")
    return f"${amount}" if product.currency.upper() == "USD" else f"{amount} {product.currency}"


def end_card_image(product: PromoProduct, photo: Path | None, cta: str) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (18, 18, 22))
    if photo is not None and photo.is_file():
        with Image.open(photo) as img:
            src = ImageOps.exif_transpose(img).convert("RGB")
        backdrop = ImageOps.fit(src, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(28))
        canvas = Image.blend(backdrop, Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0)), 0.45)
        card = ImageOps.contain(src, (560, 620))
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *card.size), radius=36, fill=255)
        canvas.paste(card, ((WIDTH - card.width) // 2, 170), mask)
    draw = ImageDraw.Draw(canvas)
    y = 840
    name_font = _font(50)
    for line in _wrap(draw, product.name, name_font, 620)[:2]:
        draw.text((WIDTH / 2, y), line, font=name_font, fill="white", anchor="mm")
        y += 62
    price = screen_price(product)
    if price:
        draw.text((WIDTH / 2, y + 30), price, font=_font(66), fill=(255, 214, 10), anchor="mm")
        y += 100
    button_font = _font(46)
    text_w = draw.textlength(cta, font=button_font)  # type: ignore[arg-type]
    box = (WIDTH / 2 - text_w / 2 - 44, y + 20, WIDTH / 2 + text_w / 2 + 44, y + 110)
    draw.rounded_rectangle(box, radius=45, fill=(238, 29, 82))
    draw.text((WIDTH / 2, (box[1] + box[3]) / 2), cta, font=button_font, fill="white", anchor="mm")
    return canvas


def end_card_clip(product: PromoProduct, photo: Path | None, cta: str, dst: Path) -> float:
    still = dst.with_suffix(".png")
    end_card_image(product, photo, cta).save(still)
    args = [
        "-y", "-loop", "1", "-t", str(END_CARD_SECONDS), "-i", str(still),
        "-f", "lavfi", "-t", str(END_CARD_SECONDS),
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", f"{_FIT},fade=t=in:st=0:d=0.3", "-map", "0:v:0", "-map", "1:a:0",
        *_ENCODE, "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    _check(_run(args), "dựng thẻ kết")
    return END_CARD_SECONDS


# ------------------------------------------------------------------ joining


def concat(parts: list[Path], dst: Path) -> None:
    listing = parts[0].with_name("concat.txt")
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
    )
    args = ["-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy"]
    _check(_run([*args, "-movflags", "+faststart", str(dst)]), "ghép video")


def thumbnail(video: Path, dst: Path, at_s: float = 1.5) -> None:
    args = ["-y", "-ss", str(at_s), "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dst)]
    _check(_run(args), "chụp ảnh xem trước")


def _stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def webvtt(
    lines: list[str],
    durations: list[float],
    spans: list[tuple[float, float] | None] | None = None,
) -> str:
    """One cue per shot: over the measured speech when known, else the whole shot."""
    cues, offset = ["WEBVTT", ""], 0.0
    spans = spans or [None] * len(lines)
    for index, (line, duration, span) in enumerate(zip(lines, durations, spans, strict=True)):
        begin, end = span if span else (0.3, max(duration - 0.3, 0.6))
        begin, end = max(0.0, min(begin, duration)), max(0.0, min(end, duration))
        if end - begin < 0.5:
            begin, end = 0.3, max(duration - 0.3, 0.6)
        cues += [str(index + 1), f"{_stamp(offset + begin)} --> {_stamp(offset + end)}", line, ""]
        offset += duration
    return "\n".join(cues)
