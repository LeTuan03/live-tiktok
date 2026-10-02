"""Media service — business logic for media file management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app.config.settings import get_settings
from app.core.events import AppEvent, event_bus
from app.core.exceptions import MediaError, MediaFileTooLargeError, MediaFileNotFoundError
from app.database.repositories.media_repo import MediaRepository, ProductMediaRepository
from app.integrations.ffmpeg.ffprobe_service import FFProbeService


# Supported media extensions
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".aac", ".m4a", ".flac", ".wma"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
ALL_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS


class MediaService:
    """Business logic for media file operations."""

    def __init__(self) -> None:
        self._repo = MediaRepository()
        self._product_media_repo = ProductMediaRepository()
        self._ffprobe = FFProbeService()
        self._settings = get_settings()

    @property
    def ffprobe_available(self) -> bool:
        return self._ffprobe.is_available()

    def import_file(self, file_path: str | Path) -> Any:
        """Import a media file into the library.

        Does NOT copy/move the original file. Stores the path reference
        and extracts metadata using ffprobe.

        Args:
            file_path: Path to the media file.

        Returns:
            The created MediaFile record.

        Raises:
            MediaFileNotFoundError: If file doesn't exist.
            MediaFileTooLargeError: If file exceeds size limit.
            MediaError: If file format is unsupported.
        """
        file_path = Path(file_path).resolve()

        # Validate file exists
        if not file_path.exists():
            raise MediaFileNotFoundError(f"File không tồn tại: {file_path}")

        # Check size
        file_size = file_path.stat().st_size
        max_size = self._settings.max_import_size_mb * 1024 * 1024
        if file_size > max_size:
            raise MediaFileTooLargeError(
                file_path.name,
                file_size / (1024 * 1024),
                self._settings.max_import_size_mb,
            )

        # Check extension
        ext = file_path.suffix.lower()
        if ext not in ALL_MEDIA_EXTENSIONS:
            raise MediaError(f"Định dạng file không được hỗ trợ: {ext}")

        # Check if already imported
        existing = self._repo.get_by_path(str(file_path))
        if existing:
            logger.info("File already imported: {}", file_path)
            return existing

        # Probe metadata
        if ext in IMAGE_EXTENSIONS:
            return self._import_image(file_path, file_size)

        probe_result = self._ffprobe.probe(file_path)
        metadata = probe_result.to_dict() if probe_result else {}

        media_type = "video" if ext in VIDEO_EXTENSIONS else "audio"
        if probe_result:
            media_type = probe_result.media_type

        data = {
            "filename": file_path.name,
            "file_path": str(file_path),
            "media_type": media_type,
            "duration_seconds": metadata.get("duration_seconds"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "codec": metadata.get("codec"),
            "file_size_bytes": file_size,
            "format_name": metadata.get("format_name"),
            "bitrate": metadata.get("bitrate"),
            "metadata_json": json.dumps(probe_result.raw if probe_result else {}, ensure_ascii=False),
            "is_valid": probe_result is not None or not self._ffprobe.is_available(),
            "error_message": None if probe_result else ("Chưa cài FFmpeg — không đọc được metadata" if not self._ffprobe.is_available() else "File không đọc được (ffprobe lỗi)"),
        }

        media = self._repo.create(data)
        event_bus.emit(AppEvent.MEDIA_IMPORTED, media_id=media.id)
        return media

    def _import_image(self, file_path: Path, file_size: int) -> Any:
        """Import a still image (product photo / presenter banner) using Pillow for dimensions."""
        width = height = None
        fmt = None
        valid = True
        error = None
        try:
            from PIL import Image

            with Image.open(file_path) as im:
                width, height = im.size
                fmt = (im.format or "").lower() or None
        except Exception as e:  # noqa: BLE001
            valid = False
            error = f"Không đọc được ảnh: {e}"
        data = {
            "filename": file_path.name,
            "file_path": str(file_path),
            "media_type": "image",
            "duration_seconds": None,
            "width": width,
            "height": height,
            "codec": fmt,
            "file_size_bytes": file_size,
            "format_name": fmt,
            "bitrate": None,
            "metadata_json": "{}",
            "is_valid": valid,
            "error_message": error,
        }
        media = self._repo.create(data)
        event_bus.emit(AppEvent.MEDIA_IMPORTED, media_id=media.id)
        return media

    def import_files(self, file_paths: list[str | Path]) -> tuple[int, list[str]]:
        """Import multiple media files.

        Returns:
            Tuple of (success count, list of error messages).
        """
        success = 0
        errors: list[str] = []

        for fp in file_paths:
            try:
                self.import_file(fp)
                success += 1
            except Exception as e:
                errors.append(f"{Path(fp).name}: {e}")

        return success, errors

    def get_media(self, media_id: int) -> Any:
        """Get a media file by ID."""
        return self._repo.get_by_id(media_id)

    def get_all_media(
        self,
        search: str = "",
        media_type: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list, int]:
        """Get all media files with filtering."""
        return self._repo.get_all(search, media_type, offset, limit)

    def delete_media(self, media_id: int) -> bool:
        """Delete a media file record (does NOT delete the actual file on disk)."""
        result = self._repo.delete(media_id)
        if result:
            event_bus.emit(AppEvent.MEDIA_DELETED, media_id=media_id)
        return result

    def count_media(self, media_type: str = "") -> int:
        """Count media files."""
        return self._repo.count(media_type)

    def check_missing_files(self) -> list[dict]:
        """Check for media records whose files no longer exist on disk."""
        all_media, _ = self._repo.get_all(limit=10000)
        missing = []
        for media in all_media:
            if not Path(media.file_path).exists():
                self._repo.mark_invalid(media.id, "File không tồn tại trên ổ đĩa")
                missing.append({
                    "id": media.id,
                    "filename": media.filename,
                    "path": media.file_path,
                })
        return missing

    # --- Product-Media Linking ---

    def link_to_product(self, media_id: int, product_id: int, is_primary: bool = False) -> Any:
        """Link a media file to a product."""
        link = self._product_media_repo.link(product_id, media_id, is_primary)
        event_bus.emit(AppEvent.MEDIA_LINKED, media_id=media_id, product_id=product_id)
        return link

    def unlink_from_product(self, media_id: int, product_id: int) -> bool:
        """Remove a media-product link."""
        return self._product_media_repo.unlink(product_id, media_id)

    def get_product_media(self, product_id: int) -> list:
        """Get all media for a product."""
        return self._product_media_repo.get_media_for_product(product_id)

    def get_product_ids_for_media(self, media_id: int) -> list[int]:
        """Get product IDs linked to a media file."""
        return self._product_media_repo.get_products_for_media(media_id)

    def get_primary_image_for_product(self, product_id: int) -> Any:
        """First valid linked image for a product, or None."""
        for m in self._product_media_repo.get_media_for_product(product_id):
            if m.media_type == "image" and m.is_valid and Path(m.file_path).exists():
                return m
        return None

    def get_primary_video_for_product(self, product_id: int) -> Any:
        """First valid linked video for a product, or None."""
        for m in self._product_media_repo.get_media_for_product(product_id):
            if m.media_type == "video" and m.is_valid and Path(m.file_path).exists():
                return m
        return None

    def revalidate(self, media_id: int) -> bool:
        """Re-probe a media file (e.g. after the user restored it) and update its record."""
        media = self._repo.get_by_id(media_id)
        if not media:
            return False
        path = Path(media.file_path)
        if not path.exists():
            self._repo.mark_invalid(media_id, "File không tồn tại trên ổ đĩa")
            return False
        if media.media_type == "image":
            self._repo.delete(media_id)
            return self.import_file(path) is not None
        probe = self._ffprobe.probe(path)
        if probe is None:
            self._repo.mark_invalid(media_id, "Không thể đọc metadata (ffprobe)")
            return False
        data = probe.to_dict()
        data.update({"is_valid": True, "error_message": None, "file_size_bytes": path.stat().st_size})
        self._repo.update(media_id, data)
        return True

    # --- Formatting helpers ---

    def format_duration(self, seconds: Optional[float]) -> str:
        return self._ffprobe.format_duration(seconds)

    def format_file_size(self, size_bytes: Optional[int]) -> str:
        return self._ffprobe.format_file_size(size_bytes)

    def format_resolution(self, width: Optional[int], height: Optional[int]) -> str:
        return self._ffprobe.format_resolution(width, height)
