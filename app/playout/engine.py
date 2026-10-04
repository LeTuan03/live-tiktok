"""Playout Engine v2 — modern event-driven playout manager for TikTok/Shopee Livestreams (§3.5, §6 P4)."""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from app.integrations.obs.client import OBSClient
from app.playout.filler import ensure_filler_clip


class PlayoutState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PlayoutItem:
    """An item in the livestream presentation playlist."""

    title: str
    video_path: str
    product_id: Optional[int] = None
    duration: float = 0.0
    price: Optional[float] = None
    promo_price: Optional[float] = None
    is_filler: bool = False
    is_insertion: bool = False  # True if inserted comment reply or flash sale
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayoutSessionStats:
    """Real-time metrics and diagnostic stats for the active livestream session."""

    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    items_presented: int = 0
    rounds_completed: int = 0
    errors_recovered: int = 0
    insertions_played: int = 0
    filler_plays: int = 0
    dropped_frames: int = 0
    current_bitrate_kbps: int = 6000

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or time.time()
        return max(0.0, end - self.start_time)


class PlayoutEngine:
    """Event-driven playout engine executing MP4 video playlists via OBS WebSocket with dynamic insertions."""

    def __init__(
        self,
        obs_client: Optional[OBSClient] = None,
        playlist: Optional[List[PlayoutItem]] = None,
        video_source_name: str = "Video Review",
        loop_playlist: bool = True,
        use_filler_fallback: bool = True,
        on_item_changed: Optional[Callable[[PlayoutItem, int, int], None]] = None,
        on_state_changed: Optional[Callable[[PlayoutState], None]] = None,
    ) -> None:
        self.obs = obs_client
        self.playlist: List[PlayoutItem] = list(playlist or [])
        self.video_source_name = video_source_name
        self.loop_playlist = loop_playlist
        self.use_filler_fallback = use_filler_fallback

        self.on_item_changed = on_item_changed
        self.on_state_changed = on_state_changed

        self.state = PlayoutState.IDLE
        self.current_index = 0
        self.current_item: Optional[PlayoutItem] = None
        self.insertion_queue: deque[PlayoutItem] = deque()
        self.stats = PlayoutSessionStats()
        self.filler_clip_path = str(ensure_filler_clip())

    def set_state(self, new_state: PlayoutState) -> None:
        self.state = new_state
        logger.info("Trạng thái PlayoutEngine đổi thành: {}", new_state.value)
        if self.on_state_changed:
            try:
                self.on_state_changed(new_state)
            except Exception as e:
                logger.debug("Lỗi callback on_state_changed: {}", e)

    def start(self) -> bool:
        """Start playing the playlist from index 0."""
        if not self.playlist:
            logger.error("Playlist rỗng, không thể bắt đầu phiên phát sóng.")
            self.set_state(PlayoutState.ERROR)
            return False

        self.stats = PlayoutSessionStats(start_time=time.time())
        self.current_index = 0
        self.set_state(PlayoutState.RUNNING)
        return self._play_next()

    def pause(self) -> bool:
        """Pause playback: pauses both video and audio simultaneously (§3.5 fix #9)."""
        if self.state != PlayoutState.RUNNING:
            return False

        if self.obs and self.obs.is_connected():
            try:
                self.obs.client.trigger_media_input_action(self.video_source_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE")
            except Exception as e:
                logger.warning("Không thể gửi lệnh PAUSE tới OBS: {}", e)

        self.set_state(PlayoutState.PAUSED)
        return True

    def resume(self) -> bool:
        """Resume playback from paused position."""
        if self.state != PlayoutState.PAUSED:
            return False

        if self.obs and self.obs.is_connected():
            try:
                self.obs.client.trigger_media_input_action(self.video_source_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_UNPAUSE")
            except Exception as e:
                logger.warning("Không thể gửi lệnh UNPAUSE tới OBS: {}", e)

        self.set_state(PlayoutState.RUNNING)
        return True

    def stop(self) -> None:
        """Stop playback and reset session."""
        if self.obs and self.obs.is_connected():
            try:
                self.obs.client.trigger_media_input_action(self.video_source_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
            except Exception:
                pass

        self.stats.end_time = time.time()
        self.set_state(PlayoutState.STOPPED)

    def skip(self) -> bool:
        """Skip current playing item and advance immediately to next."""
        if self.state not in (PlayoutState.RUNNING, PlayoutState.PAUSED):
            return False
        logger.info("Người dùng bấm chuyển mục kế tiếp (Skip).")
        return self._play_next()

    def insert_priority_clip(
        self,
        item_or_path: PlayoutItem | str | Path,
        title: str = "Clip chèn ưu tiên",
        duration: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Queue a priority clip (e.g. comment reply or flash sale) to play immediately after current item."""
        if isinstance(item_or_path, PlayoutItem):
            item = item_or_path
            p = Path(item.video_path)
        else:
            p = Path(item_or_path)
            item = PlayoutItem(
                title=title,
                video_path=str(p),
                duration=duration,
                is_insertion=True,
                metadata=metadata or {},
            )

        if not p.exists():
            logger.error("Không tìm thấy file clip chèn: {}", p)
            return False

        item.is_insertion = True
        self.insertion_queue.append(item)
        logger.info("Đã đưa clip ưu tiên vào hàng đợi phát: {}", item.title)
        return True


    def on_media_input_playback_ended(self) -> None:
        """Event handler called when OBS signals that current video playback finished (§3.5)."""
        if self.state != PlayoutState.RUNNING:
            return
        logger.info("OBS phát xong mục hiện tại, chuyển mục kế tiếp.")
        self._play_next()

    def _play_next(self) -> bool:
        """Determine and play the next item (insertion priority first, then playlist)."""
        if self.state not in (PlayoutState.RUNNING, PlayoutState.IDLE):
            return False

        # 1. Check priority insertion queue first
        if self.insertion_queue:
            next_item = self.insertion_queue.popleft()
            self.stats.insertions_played += 1
            return self._dispatch_item(next_item, is_insertion=True)

        # 2. Check playlist boundary
        if self.current_index >= len(self.playlist):
            if self.loop_playlist:
                self.current_index = 0
                self.stats.rounds_completed += 1
                logger.info("Đã hoàn thành vòng live! Bắt đầu vòng thứ {}", self.stats.rounds_completed + 1)
            else:
                logger.info("Đã phát hết playlist. Hoàn thành phiên phát sóng.")
                self.stop()
                return True

        next_item = self.playlist[self.current_index]
        self.current_index += 1
        self.stats.items_presented += 1
        return self._dispatch_item(next_item, is_insertion=False)

    def _dispatch_item(self, item: PlayoutItem, is_insertion: bool = False) -> bool:
        """Send item MP4 to OBS media source, handling missing files gracefully without stopping session."""
        self.current_item = item
        video_file = Path(item.video_path)

        # Tolerance check: if MP4 is missing, do NOT crash the live stream (§6 P4 acceptance criteria)
        if not video_file.exists() or video_file.stat().st_size < 1000:
            logger.error("Lỗi: Không tìm thấy file video review '{}' ({})", item.title, item.video_path)
            self.stats.errors_recovered += 1

            if self.use_filler_fallback and Path(self.filler_clip_path).exists():
                logger.warning("Tự động chuyển sang phát clip chờ (Filler standby) để giữ mắt người xem...")
                self.stats.filler_plays += 1
                fallback_item = PlayoutItem(
                    title="Clip Chờ Standby (Đang nạp dữ liệu)",
                    video_path=self.filler_clip_path,
                    is_filler=True,
                )
                self.current_item = fallback_item
                return self._dispatch_to_obs(fallback_item)
            else:
                # Skip to next item
                return self._play_next()

        return self._dispatch_to_obs(item)

    def _dispatch_to_obs(self, item: PlayoutItem) -> bool:
        """Update OBS media source input settings with video file path."""
        if self.obs and self.obs.is_connected():
            try:
                # Update file path on the media source
                self.obs.client.set_input_settings(
                    self.video_source_name,
                    {"local_file": str(Path(item.video_path).resolve()), "looping": item.is_filler},
                    overlay=True,
                )
                # Restart media
                self.obs.client.trigger_media_input_action(
                    self.video_source_name,
                    "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
                )
            except Exception as e:
                logger.warning("Không thể cập nhật media source trên OBS: {}", e)

        if self.on_item_changed:
            try:
                self.on_item_changed(item, self.current_index, len(self.playlist))
            except Exception as e:
                logger.debug("Lỗi callback on_item_changed: {}", e)

        return True
