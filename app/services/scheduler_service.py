"""Live orchestrator — runs a playlist of product presentations.

The orchestrator is the heart of "auto-live": for every enabled playlist item
it (1) tells OBS which video to show, (2) plays the TTS voice-over for the
product script, (3) waits for the configured duration, then (4) moves on,
optionally looping. Operators can pause/resume/stop at any time and inject a
spoken reply to a viewer comment between or during items.

Threading model
---------------
* Runs in its own ``QThread``; the UI only receives Qt signals.
* ``threading.Event`` objects implement pause/stop; waits are sliced so the
  thread reacts within ~200 ms.
* Audio is either handed to OBS (media source — captured directly in the
  stream) or emitted to the UI thread for local playback (captured by OBS
  "Desktop Audio"). The orchestrator itself never touches Qt multimedia.

Safety
------
* Consecutive failures beyond ``max_consecutive_errors`` stop the run with an
  error state — no infinite retry loops.
* Never starts/stops OBS streaming on its own; that is an explicit user action
  in the Live Studio page.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger
from PySide6.QtCore import QThread, Signal

from app.integrations.obs.client import OBSClient
from app.services.activity_log_service import log_activity
from app.services.live_session_service import LiveSessionService


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            "idle": "Chưa chạy",
            "running": "Đang chạy",
            "paused": "Tạm dừng",
            "stopping": "Đang dừng...",
            "stopped": "Đã dừng",
            "completed": "Hoàn thành",
            "error": "Lỗi",
        }[self.value]


@dataclass
class PlaylistEntry:
    """Plain-data snapshot of a playlist item (detached from SQLAlchemy)."""

    item_id: Optional[int]
    product_id: Optional[int]
    product_name: str
    video_path: Optional[str] = None
    image_path: Optional[str] = None
    price_text: str = ""
    audio_path: Optional[str] = None
    audio_duration: Optional[float] = None
    script_title: str = ""
    script_excerpt: str = ""
    duration_seconds: Optional[float] = None
    enabled: bool = True
    notes: str = ""
    price: Optional[float] = None
    promo_price: Optional[float] = None


@dataclass
class OrchestratorConfig:
    playlist_id: Optional[int]
    playlist_name: str
    entries: List[PlaylistEntry]
    loop: bool = True
    loop_count: int = 1  # used when loop is False → number of passes (>=1)
    gap_seconds: float = 5.0
    default_item_seconds: float = 90.0
    platform: str = "tiktok"
    # OBS
    use_obs: bool = False
    obs_scene: str = ""
    obs_video_source: str = ""
    obs_audio_source: str = ""
    obs_image_source: str = ""
    obs_text_source: str = ""
    show_overlay_text: bool = True
    auto_switch_scene: bool = True
    # Audio routing: "obs" (media source) or "local" (QMediaPlayer via UI)
    audio_mode: str = "local"
    max_consecutive_errors: int = 3
    use_filler_fallback: bool = False
    session_name: str = "Phiên LIVE"
    extra: Dict[str, Any] = field(default_factory=dict)


class LiveOrchestrator(QThread):
    """Background worker that plays a playlist; controlled via pause/resume/stop."""

    state_changed = Signal(str)  # RunState value
    item_started = Signal(int, object)  # index, PlaylistEntry
    item_progress = Signal(int, float, float)  # index, elapsed, total
    item_finished = Signal(int, str)  # index, status (completed|failed|skipped)
    loop_completed = Signal(int)  # pass number
    play_audio_requested = Signal(str)  # local file path → UI plays it
    stop_audio_requested = Signal()
    log_message = Signal(str, str)  # level, message
    error_occurred = Signal(str)
    finished_run = Signal(str)  # final RunState value

    def __init__(
        self,
        config: OrchestratorConfig,
        obs_client: Optional[OBSClient] = None,
        session_service: Optional[LiveSessionService] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.obs = obs_client
        self.sessions = session_service or LiveSessionService()
        self._pause_event = threading.Event()
        self._pause_event.set()  # set == not paused
        self._stop_event = threading.Event()
        self._skip_event = threading.Event()
        self._interjections: "queue.Queue[tuple[str, float]]" = queue.Queue()
        self._state = RunState.IDLE
        self._current_index = -1
        self._pass = 0
        self.session_id: Optional[int] = None
        self._local_audio_done = threading.Event()
        self._paused_total: float = 0.0

    # ------------------------------------------------------------ control
    @property
    def state(self) -> RunState:
        return self._state

    @property
    def current_index(self) -> int:
        return self._current_index

    def pause(self) -> None:
        if self._state == RunState.RUNNING:
            self._pause_event.clear()
            self._set_state(RunState.PAUSED)
            self._pause_media()
            self._log("INFO", "⏸️ Tạm dừng điều phối.")

    def resume(self) -> None:
        if self._state == RunState.PAUSED:
            self._pause_event.set()
            self._set_state(RunState.RUNNING)
            self._resume_media()
            self._log("INFO", "▶️ Tiếp tục điều phối.")

    def stop(self) -> None:
        if self._state in (RunState.RUNNING, RunState.PAUSED):
            self._set_state(RunState.STOPPING)
        self._stop_event.set()
        self._pause_event.set()  # release a paused wait so the thread can exit
        self._local_audio_done.set()

    def skip_current(self) -> None:
        self._skip_event.set()
        self._local_audio_done.set()

    def interject_audio(self, file_path: str, duration: Optional[float] = None) -> None:
        """Queue a short clip (e.g. a spoken reply) to be played after the current audio."""
        if not file_path or not os.path.exists(file_path):
            self._log("WARNING", f"Không tìm thấy file âm thanh để chèn: {file_path}")
            return
        if duration is None:
            from app.services.voice_service import estimate_mp3_duration

            duration = estimate_mp3_duration(file_path) or 8.0
        self._interjections.put((file_path, float(duration)))
        self._log("INFO", f"🎙️ Đã xếp hàng câu trả lời bằng giọng nói ({duration:.0f}s).")

    def notify_local_audio_finished(self) -> None:
        """UI calls this when QMediaPlayer finished the clip we asked it to play."""
        self._local_audio_done.set()

    # --------------------------------------------------------------- main
    def run(self) -> None:  # noqa: C901 - orchestrator state machine
        cfg = self.config
        entries = [e for e in cfg.entries if e.enabled]
        if not entries:
            self._log("ERROR", "Playlist không có mục nào được bật.")
            self._finish(RunState.ERROR)
            return

        self.session_id = self.sessions.start_session(
            cfg.session_name, cfg.playlist_id, cfg.platform, cfg.obs_scene if cfg.use_obs else ""
        )
        log_activity("session", "Bắt đầu phiên LIVE", f"Playlist: {cfg.playlist_name}", entity_type="live_session", entity_id=self.session_id)
        self._set_state(RunState.RUNNING)
        self._log("INFO", f"🔴 Bắt đầu điều phối playlist '{cfg.playlist_name}' ({len(entries)} mục).")

        if cfg.use_obs and cfg.auto_switch_scene and cfg.obs_scene and self.obs:
            if self.obs.set_current_scene(cfg.obs_scene):
                self._log("INFO", f"🎬 Đã chuyển OBS sang scene '{cfg.obs_scene}'.")
            else:
                self._log("WARNING", f"Không chuyển được scene '{cfg.obs_scene}' (OBS chưa kết nối hoặc scene không tồn tại).")

        consecutive_errors = 0
        final_state = RunState.COMPLETED
        passes_target = None if cfg.loop else max(1, int(cfg.loop_count or 1))

        try:
            while not self._stop_event.is_set():
                self._pass += 1
                for index, entry in enumerate(entries):
                    if self._stop_event.is_set():
                        break
                    self._wait_if_paused()
                    if self._stop_event.is_set():
                        break

                    self._current_index = index
                    self._skip_event.clear()
                    self.item_started.emit(index, entry)
                    self._log("INFO", f"▶️ [{index + 1}/{len(entries)}] {entry.product_name}")

                    try:
                        status = self._present(index, entry)
                        if status == "failed":
                            consecutive_errors += 1
                        else:
                            consecutive_errors = 0
                        self.item_finished.emit(index, status)
                        self.sessions.add_job(self.session_id, entry.product_id, entry.item_id, status)
                        if status == "completed":
                            self.sessions.increment(self.session_id, "products_presented")
                    except Exception as e:  # noqa: BLE001
                        consecutive_errors += 1
                        logger.exception("Lỗi khi trình chiếu mục playlist")
                        self._log("ERROR", f"Lỗi mục '{entry.product_name}': {e}")
                        self.item_finished.emit(index, "failed")
                        self.sessions.add_job(self.session_id, entry.product_id, entry.item_id, "failed", str(e))
                        self.sessions.increment(self.session_id, "error_count")

                    if consecutive_errors >= cfg.max_consecutive_errors:
                        if cfg.use_filler_fallback:
                            self._log(
                                "WARNING",
                                f"⚠️ Đã gặp {consecutive_errors} lỗi liên tiếp. Phát clip filler dự phòng để không gián đoạn livestream (Fix #17, #18).",
                            )
                            self._play_filler()
                            consecutive_errors = 0
                        else:
                            self._log("ERROR", f"Dừng vì {consecutive_errors} lỗi liên tiếp. Kiểm tra video/audio/OBS.")
                            final_state = RunState.ERROR
                            self._stop_event.set()
                            break

                    if self._stop_event.is_set():
                        break
                    # Gap between items (also a chance to play queued replies)
                    self._drain_interjections()
                    if cfg.gap_seconds > 0 and (index < len(entries) - 1 or cfg.loop or (passes_target and self._pass < passes_target)):
                        self._log("INFO", f"⏳ Nghỉ {cfg.gap_seconds:.0f}s trước mục tiếp theo...")
                        self._sleep(cfg.gap_seconds)

                if self._stop_event.is_set():
                    break
                self.loop_completed.emit(self._pass)
                if passes_target is not None and self._pass >= passes_target:
                    break
                self._log("INFO", f"🔁 Hoàn thành vòng {self._pass}, bắt đầu vòng tiếp theo.")
        finally:
            self._stop_media()
            if final_state == RunState.ERROR:
                pass
            elif self._state == RunState.STOPPING or self._stop_event.is_set():
                final_state = RunState.STOPPED
            self._finish(final_state)

    # ---------------------------------------------------------- one item
    def _present(self, index: int, entry: PlaylistEntry) -> str:
        cfg = self.config
        has_media = False
        obs_ready = cfg.use_obs and self.obs is not None

        # 0) Overlay text & Banner (L4/L6) — replaces plain text overlay with Pillow glassmorphic banner
        if obs_ready and cfg.show_overlay_text:
            if cfg.obs_text_source:
                overlay = entry.product_name + (f"\n{entry.price_text}" if entry.price_text else "")
                self.obs.set_text(cfg.obs_text_source, overlay)
            if cfg.obs_image_source:
                try:
                    from app.render.banner import BannerGenerator
                    banner_dir = Path("storage/banners")
                    banner_path = banner_dir / f"banner_p_{entry.product_id or index}.png"
                    bg = BannerGenerator()
                    bg.save_overlay(
                        product_name=entry.product_name,
                        output_path=banner_path,
                        price=entry.price,
                        promo_price=entry.promo_price,
                        is_live=True,
                    )
                    self.obs.set_image_source_file(cfg.obs_image_source, str(banner_path))
                except Exception as e:
                    logger.debug("Lỗi sinh banner cho OBS: {}", e)

        # 1) Visual: video (preferred) or still image → OBS sources
        video_ok = bool(entry.video_path and os.path.exists(entry.video_path))
        image_ok = bool(entry.image_path and os.path.exists(entry.image_path))
        if entry.video_path and not video_ok:
            self._log("WARNING", f"Video không tồn tại: {entry.video_path}")
        if obs_ready and cfg.obs_video_source:
            if video_ok:
                if self.obs.set_media_source_file(cfg.obs_video_source, entry.video_path, loop=True):
                    has_media = True
                    self._log("INFO", f"🎥 OBS đang phát video: {os.path.basename(entry.video_path)}")
                else:
                    self._log("WARNING", "Không đặt được video cho OBS media source (kiểm tra kết nối/nguồn).")
            else:
                self.obs.stop_media(cfg.obs_video_source)  # clear previous product video
            if cfg.obs_image_source and not cfg.show_overlay_text:
                if image_ok and not video_ok:
                    if self.obs.set_image_source_file(cfg.obs_image_source, entry.image_path):
                        has_media = True
                        self._log("INFO", f"🖼️ OBS đang hiển thị ảnh: {os.path.basename(entry.image_path)}")
                elif image_ok and video_ok:
                    self.obs.set_image_source_file(cfg.obs_image_source, entry.image_path)  # behind the video
                else:
                    self.obs.set_image_source_file(cfg.obs_image_source, "")
        elif video_ok or image_ok:
            name = os.path.basename(entry.video_path if video_ok else entry.image_path)
            self._log("INFO", f"🎥 Media của sản phẩm: {name} (OBS chưa kết nối — chỉ hiển thị thông tin).")

        # 2) Audio (TTS voice-over)
        audio_duration = 0.0
        audio_started = False
        if entry.audio_path:
            if not os.path.exists(entry.audio_path):
                self._log("WARNING", f"Audio không tồn tại: {entry.audio_path}")
            else:
                audio_duration = float(entry.audio_duration or 0) or self._estimate(entry.audio_path)
                audio_started = self._play_audio(entry.audio_path)
                if audio_started:
                    has_media = True
                    self._log("INFO", f"🔊 Đang đọc kịch bản ({audio_duration:.0f}s): {entry.script_title or 'TTS'}")
        else:
            self._log("INFO", "ℹ️ Mục này chưa có audio TTS — chỉ chiếu video/đếm thời gian.")

        # 3) Wait
        total = float(entry.duration_seconds or 0) or max(audio_duration, 0) or cfg.default_item_seconds
        if audio_started and audio_duration > total:
            total = audio_duration  # never cut the voice-over short
        status = self._wait_item(index, total, audio_started)

        if entry.video_path and not os.path.exists(entry.video_path) and not entry.audio_path:
            return "failed"
        if not has_media and not entry.audio_path and not entry.video_path and not entry.image_path:
            # Nothing to show at all — still counts as presented (timer only), but warn once.
            self._log("WARNING", f"Mục '{entry.product_name}' không có video lẫn audio.")
        return status

    def _wait_item(self, index: int, total: float, audio_started: bool) -> str:
        start = time.monotonic()
        paused_before = self._paused_total
        elapsed = 0.0
        last_emit = -1.0
        while True:
            if self._stop_event.is_set():
                return "skipped"
            if self._skip_event.is_set():
                self._log("INFO", "⏭️ Bỏ qua mục hiện tại.")
                self._stop_media()
                return "skipped"
            self._wait_if_paused()
            if self._stop_event.is_set():
                return "skipped"

            # Interjections (spoken replies) extend the item time.
            extra = self._drain_interjections()
            if extra:
                total += extra

            elapsed = time.monotonic() - start - (self._paused_total - paused_before)
            if elapsed - last_emit >= 0.5:
                self.item_progress.emit(index, max(0.0, elapsed), total)
                last_emit = elapsed
            if elapsed >= total:
                break
            time.sleep(0.2)
        self.item_progress.emit(index, total, total)
        return "completed"

    # ---------------------------------------------------------- helpers
    def _play_filler(self, duration: float = 6.0) -> None:
        """Play a standby filler clip so viewers don't see black screen or dropped live session (Fix #17, #18)."""
        try:
            from app.playout.filler import create_filler_clip
            filler_path = create_filler_clip()
            if self.config.use_obs and self.obs and self.config.obs_video_source:
                self.obs.set_media_source_file(self.config.obs_video_source, str(filler_path), loop=True)
                self._log("INFO", "📺 Đang phát clip filler dự phòng trên sóng.")
                self._sleep(duration)
        except Exception as e:
            self._log("WARNING", f"Không thể phát filler: {e}")

    def _wait_if_paused(self) -> None:
        if self._pause_event.is_set():
            return
        paused_at = time.monotonic()
        while not self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.1)
        self._paused_total += time.monotonic() - paused_at

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop_event.is_set() or self._skip_event.is_set():
                return
            self._wait_if_paused()
            time.sleep(0.1)

    def _estimate(self, path: str) -> float:
        from app.services.voice_service import estimate_mp3_duration

        return float(estimate_mp3_duration(path) or 0.0)

    def _play_audio(self, path: str) -> bool:
        cfg = self.config
        if cfg.audio_mode == "obs" and cfg.use_obs and self.obs and cfg.obs_audio_source:
            ok = self.obs.set_media_source_file(cfg.obs_audio_source, path, loop=False)
            if not ok:
                self._log("WARNING", "OBS không nhận file audio — chuyển sang phát âm thanh cục bộ.")
                self._local_audio_done.clear()
                self.play_audio_requested.emit(path)
                return True  # fallback to local succeeded
            return True
        # Local mode
        if not os.path.exists(path):
            self._log("ERROR", f"File audio không tồn tại: {path}")
            return False
        self._local_audio_done.clear()
        self.play_audio_requested.emit(path)
        return True

    def _drain_interjections(self) -> float:
        """Play queued reply clips sequentially. Returns total seconds spent."""
        spent = 0.0
        while not self._interjections.empty() and not self._stop_event.is_set():
            path, duration = self._interjections.get()
            self._log("INFO", f"🎙️ Đọc câu trả lời bình luận ({duration:.0f}s)...")
            # Pause product voice-over while the reply plays, then resume it.
            self._pause_media()
            self._play_audio(path)
            self._sleep(duration + 0.3)
            self._resume_media()
            spent += duration + 0.3
        return spent

    def _pause_media(self) -> None:
        """Pause BOTH audio AND video so the viewer sees a full freeze (fix #9)."""
        cfg = self.config
        if cfg.use_obs and self.obs:
            from app.integrations.obs.client import MEDIA_PAUSE

            if cfg.obs_audio_source and cfg.audio_mode == "obs":
                self.obs.media_action(cfg.obs_audio_source, MEDIA_PAUSE)
            if cfg.obs_video_source:
                self.obs.media_action(cfg.obs_video_source, MEDIA_PAUSE)
        else:
            self.stop_audio_requested.emit()

    def _resume_media(self) -> None:
        """Resume BOTH audio AND video (fix #4 — local mode was a no-op)."""
        cfg = self.config
        if cfg.use_obs and self.obs:
            from app.integrations.obs.client import MEDIA_PLAY

            if cfg.obs_audio_source and cfg.audio_mode == "obs":
                self.obs.media_action(cfg.obs_audio_source, MEDIA_PLAY)
            if cfg.obs_video_source:
                self.obs.media_action(cfg.obs_video_source, MEDIA_PLAY)

    def _stop_media(self) -> None:
        cfg = self.config
        self.stop_audio_requested.emit()
        if cfg.use_obs and self.obs:
            if cfg.obs_audio_source:
                self.obs.stop_media(cfg.obs_audio_source)

    def _set_state(self, state: RunState) -> None:
        self._state = state
        self.state_changed.emit(state.value)

    def _log(self, level: str, message: str) -> None:
        getattr(logger, level.lower(), logger.info)(message)
        self.log_message.emit(level, message)

    def _finish(self, state: RunState) -> None:
        self._set_state(state)
        self.sessions.set_status(self.session_id, state.value if state != RunState.STOPPED else "stopped")
        log_activity(
            "session",
            f"Kết thúc phiên LIVE: {state.label}",
            f"Playlist: {self.config.playlist_name}, vòng đã chạy: {self._pass}",
            level="ERROR" if state == RunState.ERROR else "INFO",
            entity_type="live_session",
            entity_id=self.session_id,
        )
        self._log("INFO", f"⏹️ Điều phối kết thúc: {state.label}.")
        self.finished_run.emit(state.value)


# --------------------------------------------------------------------------- builders
def build_entries_from_playlist(playlist, media_lookup=None) -> List[PlaylistEntry]:
    """Convert ORM playlist items into detached :class:`PlaylistEntry` objects.

    Resolution rules for each item:
    * video: item.media (video) → product's primary linked video → product.video_path
    * audio: item.audio → latest generated audio of item.script → latest audio of the
      product's current script
    """
    from app.database.repositories.media_repo import ProductMediaRepository

    entries: List[PlaylistEntry] = []
    for item in sorted(playlist.items, key=lambda i: i.sort_order):
        product = item.product
        name = product.name if product else (item.notes or f"Mục #{item.sort_order}")

        video_path = None
        image_path = None
        if item.media and item.media.media_type == "video":
            video_path = item.media.file_path
        elif item.media and item.media.media_type == "image":
            image_path = item.media.file_path
        if video_path is None and product:
            try:
                for m in ProductMediaRepository.get_media_for_product(product.id):
                    if m.media_type == "video" and m.is_valid:
                        video_path = m.file_path
                        break
            except Exception:
                pass
            if not video_path and product.video_path and os.path.exists(product.video_path):
                video_path = product.video_path
        if image_path is None and product:
            try:
                for m in ProductMediaRepository.get_media_for_product(product.id):
                    if m.media_type == "image" and m.is_valid:
                        image_path = m.file_path
                        break
            except Exception:
                pass
            if not image_path and product.image_path and os.path.exists(product.image_path):
                image_path = product.image_path
        price_text = ""
        if product:
            def _fmt(v):
                return f"{v:,.0f}đ".replace(",", ".")
            if product.promo_price and product.price and product.promo_price < product.price:
                price_text = f"Giá sốc {_fmt(product.promo_price)}  (gốc {_fmt(product.price)})"
            elif product.promo_price or product.price:
                price_text = f"Giá {_fmt(product.promo_price or product.price)}"

        audio_path = None
        audio_duration = None
        script_title = ""
        script_excerpt = ""
        script = item.script
        if item.audio and os.path.exists(item.audio.file_path):
            audio_path = item.audio.file_path
            audio_duration = item.audio.duration_seconds
            script = script or item.audio.script
        if script is None and product:
            current = [s for s in product.scripts if s.is_current] or list(product.scripts)
            script = current[0] if current else None
        if script is not None:
            script_title = script.title
            script_excerpt = (script.content or "")[:160]
            if not audio_path:
                for a in sorted(script.audio_files, key=lambda a: a.id, reverse=True):
                    if os.path.exists(a.file_path):
                        audio_path = a.file_path
                        audio_duration = a.duration_seconds
                        break

        entries.append(
            PlaylistEntry(
                item_id=item.id,
                product_id=product.id if product else None,
                product_name=name,
                video_path=video_path,
                image_path=image_path,
                price_text=price_text,
                audio_path=audio_path,
                audio_duration=audio_duration,
                script_title=script_title,
                script_excerpt=script_excerpt,
                duration_seconds=item.duration_seconds,
                enabled=bool(item.is_enabled),
                notes=item.notes or "",
                price=product.price if product else None,
                promo_price=product.promo_price if product else None,
            )
        )
    return entries
