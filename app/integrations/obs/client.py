"""OBS WebSocket 5.x client.

Design rules (see spec §7):
* Never report success for an operation that did not reach OBS.
* Never kill or close OBS; only talk to it through the WebSocket API.
* Only touch the scene/inputs that belong to LiveFlow (created on request,
  with the user's confirmation). Other scenes/sources are read-only.
* Thread-safe: the scheduler thread and the UI thread share one client.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import obsws_python as obs

    HAS_OBSWS = True
except ImportError:  # pragma: no cover
    obs = None  # type: ignore[assignment]
    HAS_OBSWS = False


MEDIA_RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
MEDIA_STOP = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
MEDIA_PLAY = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
MEDIA_PAUSE = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"


@dataclass
class OBSStatus:
    connected: bool = False
    obs_version: str = ""
    ws_version: str = ""
    current_scene: str = ""
    streaming: bool = False
    stream_timecode: str = ""
    scenes: List[str] = field(default_factory=list)
    error: str = ""


def _scene_names(scene_res: Any) -> List[str]:
    scenes = getattr(scene_res, "scenes", None) or []
    names: List[str] = []
    for s in scenes:
        if isinstance(s, dict):
            names.append(str(s.get("sceneName", s.get("scene_name", ""))))
        else:
            names.append(str(getattr(s, "scene_name", getattr(s, "sceneName", s))))
    # OBS returns scenes bottom-up; show them top-down like the OBS UI.
    return [n for n in reversed(names) if n]


class OBSClient:
    """Manager for an OBS Studio connection via obsws-python."""

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "") -> None:
        self.host = host
        self.port = port
        self.password = password
        self.req_client: Optional[Any] = None
        self.is_connected: bool = False
        self._lock = threading.RLock()
        self.last_error: str = ""

    # ----------------------------------------------------------- connection
    def configure(self, host: str, port: int, password: str = "") -> None:
        self.host = host or "localhost"
        self.port = int(port or 4455)
        self.password = password or ""

    def connect(self, timeout: float = 3.0) -> bool:
        """Connect to OBS WebSocket. Returns True only when the handshake succeeded."""
        if not HAS_OBSWS:
            self.last_error = "Thư viện 'obsws-python' chưa được cài đặt."
            logger.warning(self.last_error)
            return False

        with self._lock:
            self.disconnect(quiet=True)
            try:
                self.req_client = obs.ReqClient(
                    host=self.host, port=self.port, password=self.password, timeout=timeout
                )
                # Force a round-trip so a bad password surfaces here, not later.
                self.req_client.get_version()
                self.is_connected = True
                self.last_error = ""
                logger.info("Đã kết nối OBS WebSocket tại {}:{}", self.host, self.port)
                return True
            except Exception as e:
                self.last_error = self._friendly_error(e)
                logger.warning("Không thể kết nối OBS ({}:{}): {}", self.host, self.port, e)
                self.req_client = None
                self.is_connected = False
                return False

    def connect_with_retry(self, max_attempts: int = 3, base_delay: float = 1.0) -> bool:
        """Connect with bounded exponential backoff (1s, 2s, 4s, ...)."""
        delay = base_delay
        for attempt in range(1, max_attempts + 1):
            if self.connect():
                return True
            if attempt < max_attempts:
                logger.info("OBS reconnect attempt {}/{} in {:.0f}s", attempt, max_attempts, delay)
                time.sleep(delay)
                delay = min(delay * 2, 15.0)
        return False

    def disconnect(self, quiet: bool = False) -> None:
        with self._lock:
            if self.req_client:
                try:
                    self.req_client.disconnect()
                except Exception as e:
                    logger.debug("Lỗi khi đóng kết nối OBS: {}", e)
            self.req_client = None
            was_connected = self.is_connected
            self.is_connected = False
        if was_connected and not quiet:
            logger.info("Đã ngắt kết nối OBS WebSocket.")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke a request on the OBS client, marking the client offline on transport errors."""
        with self._lock:
            if not self.is_connected or not self.req_client:
                raise ConnectionError("Chưa kết nối OBS Studio.")
            try:
                return getattr(self.req_client, method)(*args, **kwargs)
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("connection", "closed", "socket", "timed out", "broken")):
                    self.is_connected = False
                    self.last_error = self._friendly_error(e)
                raise

    # ------------------------------------------------------------- queries
    def test_connection(self) -> tuple[bool, str, Dict[str, Any]]:
        """Open a temporary connection and return (success, message, details)."""
        if not HAS_OBSWS:
            return False, "Thư viện 'obsws-python' chưa được cài đặt trong môi trường Python.", {}

        try:
            temp_client = obs.ReqClient(
                host=self.host, port=self.port, password=self.password, timeout=3
            )
            version_res = temp_client.get_version()
            obs_version = getattr(version_res, "obs_version", "N/A")
            ws_version = getattr(version_res, "obs_web_socket_version", "N/A")
            platform = getattr(version_res, "platform", "Windows")

            scenes: List[str] = []
            current_scene = "N/A"
            try:
                scene_res = temp_client.get_scene_list()
                scenes = _scene_names(scene_res)
                current_scene = getattr(scene_res, "current_program_scene_name", "N/A")
            except Exception as se:
                logger.debug("Không thể lấy danh sách cảnh khi test OBS: {}", se)

            try:
                temp_client.disconnect()
            except Exception:
                pass

            details = {
                "obs_version": obs_version,
                "ws_version": ws_version,
                "platform": platform,
                "scenes": scenes,
                "current_scene": current_scene,
            }
            scene_info = (
                f"\n• Cảnh đang phát: {current_scene}\n• Tổng số cảnh (Scenes): {len(scenes)}"
                if scenes
                else ""
            )
            msg = (
                "Kết nối OBS Studio thành công!\n\n"
                f"• Phiên bản OBS: {obs_version}\n"
                f"• OBS WebSocket: v{ws_version}\n"
                f"• Hệ điều hành: {platform}"
                f"{scene_info}"
            )
            return True, msg, details
        except Exception as e:
            return False, f"Không thể kết nối tới OBS Studio ({self.host}:{self.port}):\n{self._friendly_error(e)}", {}

    def _friendly_error(self, e: Exception) -> str:
        error_str = str(e)
        error_lower = error_str.lower()
        if any(k in error_lower for k in ("refused", "timed out", "10061", "getaddrinfo", "unreachable")):
            return (
                f"{error_str}\n\n💡 Hướng dẫn khắc phục:\n"
                "1. Hãy đảm bảo OBS Studio đang được bật.\n"
                "2. Vào OBS → menu 'Công cụ' (Tools) → 'Cài đặt máy chủ WebSocket' "
                "(WebSocket Server Settings) → tích 'Bật máy chủ WebSocket'.\n"
                f"3. Kiểm tra Port của máy chủ có khớp với cổng {self.port} không."
            )
        if any(k in error_lower for k in ("auth", "password", "authentication", "4009")):
            return (
                f"{error_str}\n\n💡 Mật khẩu WebSocket không chính xác hoặc OBS yêu cầu xác thực. "
                "Kiểm tra lại trong OBS → Tools → WebSocket Server Settings → Show Connect Info."
            )
        return error_str

    def get_status(self) -> OBSStatus:
        """Snapshot of OBS state. ``connected=False`` with ``error`` when offline."""
        status = OBSStatus()
        if not self.is_connected:
            status.error = self.last_error or "Chưa kết nối"
            return status
        try:
            version = self._call("get_version")
            status.obs_version = getattr(version, "obs_version", "")
            status.ws_version = getattr(version, "obs_web_socket_version", "")
            scene_res = self._call("get_scene_list")
            status.scenes = _scene_names(scene_res)
            status.current_scene = getattr(scene_res, "current_program_scene_name", "")
            stream = self._call("get_stream_status")
            status.streaming = bool(getattr(stream, "output_active", False))
            status.stream_timecode = str(getattr(stream, "output_timecode", "") or "")
            status.connected = True
        except Exception as e:
            status.connected = self.is_connected
            status.error = str(e)
        return status

    def get_scenes(self) -> List[str]:
        """Scene names from OBS. Empty list when not connected (never fake data)."""
        if not self.is_connected:
            return []
        try:
            return _scene_names(self._call("get_scene_list"))
        except Exception as e:
            logger.error("Lỗi lấy danh sách Scenes từ OBS: {}", e)
            return []

    def get_current_scene(self) -> str:
        if not self.is_connected:
            return ""
        try:
            res = self._call("get_current_program_scene")
            return str(getattr(res, "current_program_scene_name", "") or getattr(res, "scene_name", ""))
        except Exception:
            return ""

    def is_streaming(self) -> Optional[bool]:
        """True/False from OBS, or None when the state cannot be determined."""
        if not self.is_connected:
            return None
        try:
            res = self._call("get_stream_status")
            return bool(getattr(res, "output_active", False))
        except Exception:
            return None

    def list_inputs(self, kind: Optional[str] = None) -> List[str]:
        if not self.is_connected:
            return []
        try:
            res = self._call("get_input_list", kind)
            names = []
            for i in getattr(res, "inputs", []) or []:
                if isinstance(i, dict):
                    names.append(str(i.get("inputName", "")))
                else:
                    names.append(str(getattr(i, "input_name", "")))
            return [n for n in names if n]
        except Exception as e:
            logger.error("Lỗi lấy danh sách input OBS: {}", e)
            return []

    # ------------------------------------------------------------ commands
    def set_current_scene(self, scene_name: str) -> bool:
        """Switch program scene. False when offline or OBS rejected the request."""
        if not self.is_connected:
            logger.warning("Bỏ qua chuyển cảnh '{}' — chưa kết nối OBS.", scene_name)
            return False
        try:
            self._call("set_current_program_scene", scene_name)
            logger.info("Đã chuyển cảnh OBS sang: {}", scene_name)
            return True
        except Exception as e:
            logger.error("Lỗi chuyển cảnh OBS: {}", e)
            return False

    def set_media_source_file(self, input_name: str, file_path: str, loop: bool = False) -> bool:
        """Point an ``ffmpeg_source`` input at a local file and restart playback."""
        if not self.is_connected:
            return False
        try:
            self._call(
                "set_input_settings",
                input_name,
                {
                    "is_local_file": True,
                    "local_file": file_path,
                    "file": file_path,
                    "looping": bool(loop),
                    "restart_on_activate": True,
                    "clear_on_media_end": False,
                },
                True,
            )
            try:
                self._call("trigger_media_input_action", input_name, MEDIA_RESTART)
            except Exception:
                pass
            logger.info("OBS media '{}' ← {}", input_name, file_path)
            return True
        except Exception as e:
            logger.error("Lỗi đặt file cho media source '{}': {}", input_name, e)
            return False

    def set_media_file(self, input_name: str, file_path: str, loop: bool = False) -> bool:
        """Alias for set_media_source_file."""
        return self.set_media_source_file(input_name=input_name, file_path=file_path, loop=loop)

    def media_action(self, input_name: str, action: str) -> bool:
        if not self.is_connected:
            return False
        try:
            self._call("trigger_media_input_action", input_name, action)
            return True
        except Exception as e:
            logger.error("Lỗi điều khiển media '{}': {}", input_name, e)
            return False

    def stop_media(self, input_name: str) -> bool:
        return self.media_action(input_name, MEDIA_STOP)

    def get_media_state(self, input_name: str) -> Dict[str, Any]:
        """Return {'state', 'duration_ms', 'cursor_ms'} or {} when unavailable."""
        if not self.is_connected:
            return {}
        try:
            res = self._call("get_media_input_status", input_name)
            return {
                "state": str(getattr(res, "media_state", "")),
                "duration_ms": getattr(res, "media_duration", None),
                "cursor_ms": getattr(res, "media_cursor", None),
            }
        except Exception:
            return {}

    def set_image_source_file(self, input_name: str, file_path: str) -> bool:
        """Point an ``image_source`` input at a file (empty path clears it)."""
        if not self.is_connected:
            return False
        try:
            self._call("set_input_settings", input_name, {"file": file_path or ""}, True)
            return True
        except Exception as e:
            logger.error("Lỗi đặt ảnh cho image source '{}': {}", input_name, e)
            return False

    def set_image_file(self, input_name: str, file_path: str) -> bool:
        """Alias for set_image_source_file."""
        return self.set_image_source_file(input_name=input_name, file_path=file_path)

    def set_text(self, input_name: str, text: str) -> bool:
        """Update a GDI+/FreeType text source."""
        if not self.is_connected:
            return False
        try:
            self._call("set_input_settings", input_name, {"text": text or ""}, True)
            return True
        except Exception as e:
            logger.error("Lỗi cập nhật text source '{}': {}", input_name, e)
            return False

    def set_avatar_media_file(
        self,
        input_name: str,
        file_path: str,
        loop: bool = True,
        chroma_key: bool = True,
        scene_name: str = "",
        position: str = "bottom_right",
        scale: float = 0.38,
    ) -> bool:
        """Point an avatar input at a video/photo file, optionally updating position and chroma key."""
        if not self.is_connected:
            return False
        try:
            settings: dict[str, Any] = {
                "is_local_file": True,
                "local_file": file_path or "",
                "file": file_path or "",
                "looping": loop,
                "restart_on_activate": True,
                "clear_on_media_end": False,
            }
            self._call("set_input_settings", input_name, settings, True)
            try:
                self._call("trigger_media_input_action", input_name, MEDIA_RESTART)
            except Exception:
                pass
            if chroma_key:
                self.set_source_chroma_key(input_name, enabled=True)
            if scene_name:
                self._place_avatar_overlay(scene_name, input_name, position=position, scale=scale)
            return True
        except Exception as e:
            logger.error("Lỗi đặt media cho avatar '{}': {}", input_name, e)
            return False

    def set_source_chroma_key(
        self,
        source_name: str,
        enabled: bool = True,
        key_color_type: str = "green",
        similarity: int = 400,
        smoothness: int = 80,
    ) -> bool:
        """Add or toggle Chroma Key filter on a source (used for green screen avatars)."""
        if not self.is_connected:
            return False
        filter_name = "Chroma Key (LiveFlow)"
        try:
            # Check existing filters
            res = self._call("get_source_filter_list", source_name)
            filters = [f.get("filterName") if isinstance(f, dict) else getattr(f, "filter_name", "") for f in (getattr(res, "filters", []) or [])]
            if filter_name not in filters:
                self._call(
                    "create_source_filter",
                    source_name,
                    filter_name,
                    "chroma_key_filter",
                    {
                        "key_color_type": key_color_type,
                        "similarity": similarity,
                        "smoothness": smoothness,
                    },
                )
            else:
                self._call(
                    "set_source_filter_enabled",
                    source_name,
                    filter_name,
                    enabled,
                )
            return True
        except Exception as e:
            logger.debug("Không thể áp dụng chroma key filter trên '{}': {}", source_name, e)
            return False

    def set_scene_item_visible(self, scene_name: str, source_name: str, visible: bool) -> bool:
        """Toggle visibility of a source in a scene."""
        if not self.is_connected:
            return False
        try:
            item = self._call("get_scene_item_id", scene_name, source_name)
            item_id = getattr(item, "scene_item_id", None)
            if item_id is None:
                return False
            self._call("set_scene_item_enabled", scene_name, item_id, visible)
            return True
        except Exception as e:
            logger.debug("Không thể đổi trạng thái hiển thị của '{}' trong scene '{}': {}", source_name, scene_name, e)
            return False

    def ensure_liveflow_setup(
        self,
        scene_name: str,
        video_source: str,
        audio_source: str,
        image_source: str = "",
        text_source: str = "",
        avatar_source: str = "",
    ) -> tuple[bool, str]:
        """Create the LiveFlow scene and media inputs if they do not exist.

        Only creates; never modifies or deletes anything that already exists.
        """
        if not self.is_connected:
            return False, "Chưa kết nối OBS Studio."
        created: list[str] = []
        try:
            scenes = self.get_scenes()
            if scene_name not in scenes:
                self._call("create_scene", scene_name)
                created.append(f"Scene '{scene_name}'")

            inputs = set(self.list_inputs())
            # Z-order: image (bottom) → video → avatar → text (top). OBS puts newer items on top.
            if image_source:
                if image_source not in inputs:
                    self._call("create_input", scene_name, image_source, "image_source", {"file": ""}, True)
                    created.append(f"Image source '{image_source}'")
                else:
                    self._ensure_in_scene(scene_name, image_source)
            if video_source not in inputs:
                self._call(
                    "create_input",
                    scene_name,
                    video_source,
                    "ffmpeg_source",
                    {"is_local_file": True, "looping": True, "restart_on_activate": True, "clear_on_media_end": True},
                    True,
                )
                created.append(f"Media source '{video_source}'")
            else:
                self._ensure_in_scene(scene_name, video_source)

            if audio_source not in inputs:
                self._call(
                    "create_input",
                    scene_name,
                    audio_source,
                    "ffmpeg_source",
                    {"is_local_file": True, "looping": False, "restart_on_activate": True},
                    True,
                )
                created.append(f"Audio source '{audio_source}'")
            else:
                self._ensure_in_scene(scene_name, audio_source)

            if avatar_source:
                if avatar_source not in inputs:
                    self._call(
                        "create_input",
                        scene_name,
                        avatar_source,
                        "ffmpeg_source",
                        {"is_local_file": True, "looping": True, "restart_on_activate": True, "clear_on_media_end": True},
                        True,
                    )
                    created.append(f"Người mẫu AI '{avatar_source}'")
                else:
                    self._ensure_in_scene(scene_name, avatar_source)

            if text_source:
                if text_source not in inputs:
                    self._call(
                        "create_input",
                        scene_name,
                        text_source,
                        "text_gdiplus_v3" if self._has_input_kind("text_gdiplus_v3") else ("text_gdiplus_v2" if self._has_input_kind("text_gdiplus_v2") else "text_ft2_source_v2"),
                        {
                            "text": "LiveFlow AI",
                            "font": {"face": "Segoe UI", "size": 56, "style": "Bold"},
                            "color": 4294967295,
                            "outline": True,
                            "outline_color": 4278190080,
                            "outline_size": 4,
                            "bk_opacity": 0,
                        },
                        True,
                    )
                    created.append(f"Text source '{text_source}'")
                else:
                    self._ensure_in_scene(scene_name, text_source)

            # Fit image/video to the canvas so the product fills the frame (9:16 or 16:9).
            if image_source:
                self._fit_to_canvas(scene_name, image_source)
            self._fit_to_canvas(scene_name, video_source)
            if avatar_source:
                self._place_avatar_overlay(scene_name, avatar_source)
            if text_source:
                self._place_text_bottom(scene_name, text_source)

            if created:
                msg = "Đã tạo trong OBS: " + ", ".join(created)
            else:
                msg = "Cấu hình LiveFlow trong OBS đã đầy đủ (không thay đổi gì)."
            logger.info(msg)
            return True, msg
        except Exception as e:
            logger.error("Lỗi tạo cấu hình LiveFlow trong OBS: {}", e)
            return False, f"Lỗi tạo cấu hình trong OBS: {e}"

    def _has_input_kind(self, kind: str) -> bool:
        try:
            res = self._call("get_input_kind_list", False)  # versioned ids, e.g. text_gdiplus_v3
            kinds = [str(k) for k in (getattr(res, "input_kinds", []) or [])]
            return kind in kinds
        except Exception:
            return False

    def _place_text_bottom(self, scene_name: str, source_name: str) -> None:
        """Anchor the text overlay near the bottom-left of the canvas."""
        try:
            video = self._call("get_video_settings")
            width = getattr(video, "base_width", None)
            height = getattr(video, "base_height", None)
            item = self._call("get_scene_item_id", scene_name, source_name)
            item_id = getattr(item, "scene_item_id", None)
            if not (width and height and item_id is not None):
                return
            self._call(
                "set_scene_item_transform",
                scene_name,
                item_id,
                {"positionX": int(width * 0.05), "positionY": int(height * 0.78), "alignment": 5},
            )
        except Exception as e:
            logger.debug("Không thể đặt vị trí text overlay: {}", e)

    def _place_avatar_overlay(
        self, scene_name: str, source_name: str, position: str = "bottom_right", scale: float = 0.38
    ) -> None:
        """Place the AI Avatar on the canvas (default: bottom-right overlay)."""
        try:
            video = self._call("get_video_settings")
            width = getattr(video, "base_width", None)
            height = getattr(video, "base_height", None)
            item = self._call("get_scene_item_id", scene_name, source_name)
            item_id = getattr(item, "scene_item_id", None)
            if not (width and height and item_id is not None):
                return

            avatar_w = int(width * scale)
            avatar_h = int(height * scale * 1.3)  # Slightly taller aspect

            if position == "bottom_left":
                pos_x = int(width * 0.03)
                pos_y = int(height - avatar_h)
            elif position == "center":
                pos_x = int((width - avatar_w) / 2)
                pos_y = int(height * 0.15)
            elif position == "full":
                pos_x = 0
                pos_y = 0
                avatar_w = width
                avatar_h = height
            else:  # bottom_right (standard TikTok live streamer overlay)
                pos_x = int(width - avatar_w)
                pos_y = int(height - avatar_h)

            self._call(
                "set_scene_item_transform",
                scene_name,
                item_id,
                {
                    "positionX": pos_x,
                    "positionY": pos_y,
                    "boundsType": "OBS_BOUNDS_SCALE_INNER",
                    "boundsAlignment": 0,
                    "boundsWidth": avatar_w,
                    "boundsHeight": avatar_h,
                },
            )
        except Exception as e:
            logger.debug("Không thể đặt vị trí avatar overlay: {}", e)

    def _ensure_in_scene(self, scene_name: str, source_name: str) -> None:
        """If an existing input is not part of the LiveFlow scene, add it (no-op otherwise)."""
        try:
            self._call("get_scene_item_id", scene_name, source_name)
        except Exception:
            try:
                self.req_client.create_scene_item(scene_name, source_name, True)  # type: ignore[union-attr]
            except Exception as e:
                logger.debug("Không thể thêm '{}' vào scene '{}': {}", source_name, scene_name, e)

    def _fit_to_canvas(self, scene_name: str, source_name: str) -> None:
        try:
            video = self._call("get_video_settings")
            width = getattr(video, "base_width", None)
            height = getattr(video, "base_height", None)
            item = self._call("get_scene_item_id", scene_name, source_name)
            item_id = getattr(item, "scene_item_id", None)
            if not (width and height and item_id is not None):
                return
            self._call(
                "set_scene_item_transform",
                scene_name,
                item_id,
                {
                    "positionX": 0,
                    "positionY": 0,
                    "boundsType": "OBS_BOUNDS_SCALE_INNER",
                    "boundsAlignment": 0,
                    "boundsWidth": width,
                    "boundsHeight": height,
                },
            )
        except Exception as e:
            logger.debug("Không thể căn media source theo canvas: {}", e)

    # ------------------------------------------------------------ streaming
    def set_stream_service(self, server: str, stream_key: str) -> bool:
        """Configure OBS to stream to a custom RTMP endpoint (TikTok / Shopee)."""
        if not self.is_connected:
            return False
        try:
            self._call(
                "set_stream_service_settings",
                "rtmp_custom",
                {"server": server, "key": stream_key, "use_auth": False},
            )
            logger.info("Đã cập nhật RTMP server cho OBS: {}", server)
            return True
        except Exception as e:
            logger.error("Lỗi cấu hình stream service OBS: {}", e)
            return False

    def start_stream(self) -> tuple[bool, str]:
        if not self.is_connected:
            return False, "Chưa kết nối OBS Studio."
        try:
            if self.is_streaming():
                return True, "OBS đã đang phát trực tiếp."
            self._call("start_stream")
            # Confirm with OBS instead of assuming success.
            for _ in range(10):
                time.sleep(0.5)
                if self.is_streaming():
                    return True, "OBS đã bắt đầu phát trực tiếp."
            return False, "OBS chưa xác nhận bắt đầu stream. Kiểm tra Stream Key và kết nối mạng."
        except Exception as e:
            return False, f"Không thể bắt đầu stream: {e}"

    def stop_stream(self) -> tuple[bool, str]:
        if not self.is_connected:
            return False, "Chưa kết nối OBS Studio."
        try:
            if self.is_streaming() is False:
                return True, "OBS hiện không phát trực tiếp."
            self._call("stop_stream")
            for _ in range(10):
                time.sleep(0.5)
                if self.is_streaming() is False:
                    return True, "OBS đã dừng phát trực tiếp."
            return False, "OBS chưa xác nhận dừng stream."
        except Exception as e:
            return False, f"Không thể dừng stream: {e}"
