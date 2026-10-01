"""OBS WebSocket 5.x Client integration."""

from __future__ import annotations

from typing import List, Optional, Dict, Any
from loguru import logger

try:
    import obsws_python as obs
    HAS_OBSWS = True
except ImportError:
    HAS_OBSWS = False


class OBSClient:
    """Manager for OBS Studio connection via obsws-python library."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.req_client: Optional[Any] = None
        self.is_connected: bool = False

    def connect(self) -> bool:
        """Connect to OBS WebSocket server."""
        if not HAS_OBSWS:
            logger.warning("Thư viện 'obsws-python' chưa được cài đặt.")
            return False

        try:
            self.req_client = obs.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password,
                timeout=3,
            )
            self.is_connected = True
            logger.info(f"Đã kết nối OBS WebSocket tại {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.warning(f"Không thể kết nối tới OBS Studio ({self.host}:{self.port}): {e}")
            self.is_connected = False
            self.req_client = None
            return False

    def disconnect(self) -> None:
        """Disconnect from OBS Studio."""
        if self.req_client:
            try:
                self.req_client.disconnect()
            except Exception as e:
                logger.debug(f"Lỗi khi đóng kết nối OBS: {e}")
        self.req_client = None
        self.is_connected = False
        logger.info("Đã ngắt kết nối OBS WebSocket.")

    def test_connection(self) -> tuple[bool, str, Dict[str, Any]]:
        """Test connection to OBS WebSocket server and return status & diagnostic details.

        Returns:
            tuple[bool, str, dict]: (success, message, info_dict)
        """
        if not HAS_OBSWS:
            return False, "Thư viện 'obsws-python' chưa được cài đặt trong môi trường Python.", {}

        try:
            temp_client = obs.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password,
                timeout=3,
            )
            version_res = temp_client.get_version()
            obs_version = getattr(version_res, "obs_version", "N/A")
            ws_version = getattr(version_res, "obs_web_socket_version", "N/A")
            platform = getattr(version_res, "platform", "Windows")

            scenes: List[str] = []
            current_scene = "N/A"
            try:
                scene_res = temp_client.get_scene_list()
                if hasattr(scene_res, "scenes") and scene_res.scenes:
                    scenes = [
                        s.get("sceneName", str(s)) if isinstance(s, dict) else getattr(s, "scene_name", str(s))
                        for s in scene_res.scenes
                    ]
                current_scene = getattr(scene_res, "current_program_scene_name", "N/A")
            except Exception as se:
                logger.debug(f"Không thể lấy danh sách cảnh khi test OBS: {se}")

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
            scene_info = f"\n• Cảnh đang phát: {current_scene}\n• Tổng số cảnh (Scenes): {len(scenes)}" if scenes else ""
            msg = (
                f"Kết nối OBS Studio thành công!\n\n"
                f"• Phiên bản OBS: {obs_version}\n"
                f"• OBS WebSocket: v{ws_version}\n"
                f"• Hệ điều hành: {platform}"
                f"{scene_info}"
            )
            return True, msg, details
        except Exception as e:
            error_str = str(e)
            hint = ""
            error_lower = error_str.lower()
            if "refused" in error_lower or "timed out" in error_lower or "winerror 10061" in error_lower:
                hint = (
                    "\n\n💡 Hướng dẫn khắc phục:\n"
                    "1. Hãy đảm bảo phần mềm OBS Studio đang được bật.\n"
                    "2. Kiểm tra WebSocket Server đã bật chưa: Vào OBS -> 'Công cụ' (Tools) -> "
                    "'Cài đặt máy chủ WebSocket' (WebSocket Server Settings) -> Tích chọn 'Bật máy chủ WebSocket' (Enable WebSocket server).\n"
                    f"3. Kiểm tra Port của máy chủ có khớp với cổng {self.port} không."
                )
            elif "auth" in error_lower or "password" in error_lower or "authentication" in error_lower:
                hint = (
                    "\n\n💡 Hướng dẫn khắc phục:\n"
                    "Mật khẩu WebSocket không chính xác hoặc OBS yêu cầu xác thực. Vui lòng kiểm tra lại mật khẩu "
                    "trong menu OBS -> Tools -> WebSocket Server Settings."
                )
            return False, f"Không thể kết nối tới OBS Studio ({self.host}:{self.port}):\n{error_str}{hint}", {}

    def get_scenes(self) -> List[str]:
        """Get list of scene names from OBS."""
        if not self.is_connected or not self.req_client:
            return ["Main Live Scene", "Product Showcase", "BRB / Break"]

        try:
            res = self.req_client.get_scene_list()
            scenes = [
                s.get("sceneName", str(s)) if isinstance(s, dict) else getattr(s, "scene_name", str(s))
                for s in res.scenes
            ]
            return scenes
        except Exception as e:
            logger.error(f"Lỗi lấy danh sách Scenes từ OBS: {e}")
            return ["Main Live Scene", "Product Showcase"]

    def set_current_scene(self, scene_name: str) -> bool:
        """Switch current active scene in OBS."""
        if not self.is_connected or not self.req_client:
            logger.info(f"[Mock OBS] Chuyển cảnh sang: {scene_name}")
            return True

        try:
            self.req_client.set_current_program_scene(scene_name)
            logger.info(f"Đã chuyển cảnh OBS sang: {scene_name}")
            return True
        except Exception as e:
            logger.error(f"Lỗi chuyển cảnh OBS: {e}")
            return False
