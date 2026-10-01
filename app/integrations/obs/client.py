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
        self.req_client = None
        self.is_connected = False
        logger.info("Đã ngắt kết nối OBS WebSocket.")

    def get_scenes(self) -> List[str]:
        """Get list of scene names from OBS."""
        if not self.is_connected or not self.req_client:
            return ["Main Live Scene", "Product Showcase", "BRB / Break"]

        try:
            res = self.req_client.get_scene_list()
            scenes = [s["sceneName"] for s in res.scenes]
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
