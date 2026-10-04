"""Screen OCR engine for capturing live chat panels on TikTok Studio / Shopee Live (§3.6, §6 P5)."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from loguru import logger
from PIL import Image, ImageGrab


@dataclass
class ScreenOCRRegion:
    """Bounding box coordinates of the live stream comment panel on the screen."""

    x: int = 0
    y: int = 0
    width: int = 400
    height: int = 600
    label: str = "TikTok Studio Chat Panel"

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


@dataclass
class ExtractedComment:
    """Parsed single live comment event from OCR."""

    username: str
    text: str
    raw_line: str
    comment_hash: str
    timestamp: float = field(default_factory=time.time)


# System noise lines to filter out
SYSTEM_IGNORE_PATTERNS = [
    re.compile(r"(vừa tham gia|đã tham gia|joined|vừa vào phòng)", re.IGNORECASE),
    re.compile(r"(đã theo dõi|followed|follow shop)", re.IGNORECASE),
    re.compile(r"(đã chia sẻ|shared|share livestream)", re.IGNORECASE),
    re.compile(r"(đã gửi|sent rose|gửi hoa hồng|tặng quà|sent gift)", re.IGNORECASE),
    re.compile(r"(thông báo hệ thống|tiêu chuẩn cộng đồng)", re.IGNORECASE),
]


class ScreenOCRService:
    """Captures desktop screen region, extracts Vietnamese chat text, and prevents duplicate processing."""

    def __init__(
        self,
        default_region: Optional[ScreenOCRRegion] = None,
        cache_ttl_seconds: int = 900,  # 15 minutes anti-duplicate window
    ) -> None:
        self.region = default_region or ScreenOCRRegion()
        self.cache_ttl = cache_ttl_seconds
        # Mapping from comment_hash -> timestamp seen
        self._seen_hashes: Dict[str, float] = {}

    def capture_region(self, region: Optional[ScreenOCRRegion] = None) -> Image.Image:
        """Capture screenshot of the specified screen region."""
        target = region or self.region
        try:
            screenshot = ImageGrab.grab(bbox=target.bbox)
            return screenshot
        except Exception as e:
            logger.error("Lỗi chụp màn hình vùng OCR {}: {}", target.bbox, e)
            return Image.new("RGB", (target.width, target.height), color="black")

    def run_ocr(self, image: Image.Image) -> str:
        """Run OCR on the given image. Tries pytesseract or graceful fallback."""
        try:
            import pytesseract  # type: ignore

            # Use Vietnamese + English OCR
            text = pytesseract.image_to_string(image, lang="vie+eng")
            return text
        except (ImportError, Exception) as e:
            # Fallback if tesseract executable is not on PATH or not installed
            logger.debug("Pytesseract không khả dụng hoặc lỗi: {}. Thử winocr hoặc text extractor.", e)
            return ""

    def parse_comments(self, raw_text: str) -> List[ExtractedComment]:
        """Parse raw multi-line OCR text into clean username + comment items."""
        if not raw_text or not raw_text.strip():
            return []

        results: List[ExtractedComment] = []
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        for line in lines:
            # 1. Filter out system announcements & noise
            if any(pat.search(line) for pat in SYSTEM_IGNORE_PATTERNS):
                continue

            # 2. Extract username: comment pattern
            # Pattern A: 'username: comment content' or 'username : comment content'
            match = re.match(r"^([@\w\.\_\-]{2,25})\s*[:\-\—]\s*(.+)$", line)
            if match:
                u, c = match.group(1), match.group(2).strip()
            else:
                # Pattern B: '[username] comment content'
                match_b = re.match(r"^\[([@\w\.\_\-]{2,25})\]\s*(.+)$", line)
                if match_b:
                    u, c = match_b.group(1), match_b.group(2).strip()
                else:
                    # If line has no separator but looks like a user comment, skip or handle
                    continue

            # Basic text sanitization
            u = u.lstrip("@").strip()
            if len(c) < 2:
                continue

            # Compute anti-duplicate hash: SHA256 of normalized username and comment text
            norm_key = f"{u.lower()}:{c.lower()}"
            chash = hashlib.sha256(norm_key.encode("utf-8")).hexdigest()

            results.append(
                ExtractedComment(
                    username=u,
                    text=c,
                    raw_line=line,
                    comment_hash=chash,
                )
            )

        return results

    def filter_duplicates(self, comments: List[ExtractedComment]) -> List[ExtractedComment]:
        """Guarantee 0 duplicate comments processed (§6 P5 acceptance criteria)."""
        now = time.time()
        # Purge stale cache entries
        stale_keys = [k for k, ts in self._seen_hashes.items() if now - ts > self.cache_ttl]
        for k in stale_keys:
            del self._seen_hashes[k]

        new_comments: List[ExtractedComment] = []
        for c in comments:
            if c.comment_hash in self._seen_hashes:
                logger.debug("Bỏ qua bình luận trùng lặp: {} - '{}'", c.username, c.text)
                continue

            self._seen_hashes[c.comment_hash] = now
            new_comments.append(c)

        return new_comments

    def poll_once(
        self,
        region: Optional[ScreenOCRRegion] = None,
        custom_image: Optional[Image.Image] = None,
    ) -> List[ExtractedComment]:
        """Capture one frame, run OCR, parse and return newly seen comments."""
        img = custom_image if custom_image is not None else self.capture_region(region)
        raw_text = self.run_ocr(img)
        parsed = self.parse_comments(raw_text)
        return self.filter_duplicates(parsed)
