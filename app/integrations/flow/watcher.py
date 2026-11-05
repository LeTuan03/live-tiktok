"""Canh thư mục tải về để bắt video mới từ Flow.

Dùng làm *lưới an toàn*: khi trình duyệt nhúng trong app không bắt được file
(hoặc người vận hành tải bằng Chrome thật), LiveFlow vẫn tự nhận file mới.

Chỉ trả về file đã ghi xong: kích thước phải không đổi giữa hai lần kiểm tra.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from loguru import logger

from app.integrations.flow.importer import IMAGE_SUFFIXES, VIDEO_SUFFIXES

# Đuôi file tạm của trình duyệt khi đang tải — bỏ qua.
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")


def default_download_dirs() -> List[Path]:
    """Thư mục mặc định cần canh: cấu hình trong .env, rồi tới Downloads của Windows."""
    dirs: List[Path] = []
    try:
        from app.config.settings import get_settings

        configured = get_settings().flow_downloads_dir
        if configured:
            dirs.append(Path(configured))
    except Exception:
        pass

    home = Path.home()
    for name in ("Downloads", "Tải xuống"):
        p = home / name
        if p.is_dir():
            dirs.append(p)

    seen: set[str] = set()
    out: List[Path] = []
    for d in dirs:
        key = str(d).lower()
        if d.is_dir() and key not in seen:
            seen.add(key)
            out.append(d)
    return out


class FlowDownloadWatcher:
    """Theo dõi các thư mục và trả về file video/ảnh mới xuất hiện."""

    def __init__(
        self,
        directories: Optional[Sequence[Path | str]] = None,
        *,
        since: Optional[float] = None,
        suffixes: Iterable[str] = VIDEO_SUFFIXES + IMAGE_SUFFIXES,
        settle_seconds: float = 1.5,
    ) -> None:
        self.directories = [Path(d) for d in (directories or default_download_dirs())]
        self.since = since if since is not None else time.time()
        self.suffixes = tuple(s.lower() for s in suffixes)
        self.settle_seconds = settle_seconds
        self._seen: set[str] = set()
        self._sizes: Dict[str, int] = {}

    def prime(self) -> None:
        """Đánh dấu mọi file đang có là 'đã thấy' — chỉ bắt file xuất hiện từ giờ."""
        for f in self._candidates():
            self._seen.add(str(f).lower())

    def _candidates(self) -> List[Path]:
        found: List[Path] = []
        for d in self.directories:
            if not d.is_dir():
                continue
            try:
                for f in d.iterdir():
                    if not f.is_file():
                        continue
                    name = f.name.lower()
                    if name.endswith(PARTIAL_SUFFIXES):
                        continue
                    if f.suffix.lower() not in self.suffixes:
                        continue
                    found.append(f)
            except Exception as e:  # thư mục bị khoá quyền
                logger.debug("Không đọc được thư mục {}: {}", d, e)
        return found

    def poll(self) -> List[Path]:
        """Trả về danh sách file mới đã ghi xong, cũ nhất trước."""
        ready: List[Path] = []
        for f in self._candidates():
            key = str(f).lower()
            if key in self._seen:
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            if stat.st_mtime < self.since - 1:
                self._seen.add(key)  # file cũ, không phải của phiên này
                continue

            prev = self._sizes.get(key)
            if prev is None or prev != stat.st_size:
                self._sizes[key] = stat.st_size
                continue  # còn đang tải, chờ lần kiểm tra sau
            if time.time() - stat.st_mtime < self.settle_seconds:
                continue

            self._seen.add(key)
            self._sizes.pop(key, None)
            ready.append(f)

        ready.sort(key=lambda p: p.stat().st_mtime)
        return ready

    def forget(self, path: Path | str) -> None:
        """Quên một file để có thể nhận lại (dùng khi nạp thất bại và cần thử lại).

        Lần ``poll()`` sau đó chỉ ghi nhận lại kích thước; phải gọi ``poll()`` lần nữa
        mới trả về file — đúng quy tắc "file phải ngừng thay đổi mới nhận".
        """
        self._seen.discard(str(Path(path)).lower())

    def mark_seen(self, path: Path | str) -> None:
        """Đánh dấu đã xử lý file này — dùng khi trình duyệt nhúng đã tự nạp xong."""
        self._seen.add(str(Path(path)).lower())
        self._sizes.pop(str(Path(path)).lower(), None)
