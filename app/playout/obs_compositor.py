"""OBS Compositor — enforces 1080x1920 vertical canvas, deterministic z-order, and standard scene setup."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.integrations.obs.client import OBSClient


DEFAULT_SCENE = "LiveFlow Live"
DEFAULT_PLAYER_SOURCE = "LiveFlow Player"
DEFAULT_BANNER_SOURCE = "LiveFlow Banner"
DEFAULT_AVATAR_SOURCE = "LiveFlow Avatar"

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_FPS = 30


class OBSCompositor:
    """Manages scene setup, layer ordering, and vertical canvas enforcement in OBS Studio."""

    def __init__(self, obs_client: OBSClient) -> None:
        self.obs = obs_client

    def enforce_canvas_format(
        self,
        width: int = CANVAS_WIDTH,
        height: int = CANVAS_HEIGHT,
        fps: int = CANVAS_FPS,
    ) -> Tuple[bool, str]:
        """Force OBS canvas to vertical 1080x1920 (9:16) at target FPS (default 30) and verify immediately."""
        if not self.obs.is_connected:
            return False, "Chưa kết nối OBS Studio."

        current = self.obs.get_video_settings()
        if (
            current.get("base_width") == width
            and current.get("base_height") == height
            and int(current.get("fps", 0)) == fps
        ):
            return True, f"Canvas OBS đã chuẩn: {width}×{height} @ {fps}fps."

        return self.obs.set_video_settings(
            base_width=width,
            base_height=height,
            out_width=width,
            out_height=height,
            fps_num=fps,
            fps_den=1,
        )

    def setup_standard_scene(
        self,
        scene_name: str = DEFAULT_SCENE,
        player_source: str = DEFAULT_PLAYER_SOURCE,
        banner_source: str = DEFAULT_BANNER_SOURCE,
        avatar_source: str = "",
    ) -> Tuple[bool, str]:
        """Ensure the standard LiveFlow 9:16 scene is created with deterministic layers and fitted to canvas."""
        if not self.obs.is_connected:
            return False, "Chưa kết nối OBS Studio."

        created: list[str] = []
        try:
            # 1. Ensure scene exists
            scenes = self.obs.get_scenes()
            if scene_name not in scenes:
                self.obs._call("create_scene", scene_name)
                created.append(f"Scene '{scene_name}'")

            existing_inputs = set(self.obs.list_inputs())

            # 2. Player media source (bottom-most layer)
            if player_source not in existing_inputs:
                self.obs._call(
                    "create_input",
                    scene_name,
                    player_source,
                    "ffmpeg_source",
                    {
                        "is_local_file": True,
                        "looping": False,
                        "restart_on_activate": True,
                        "clear_on_media_end": False,
                    },
                    True,
                )
                created.append(f"Media player '{player_source}'")
            else:
                self.obs._ensure_in_scene(scene_name, player_source)

            # 3. Avatar source (middle layer, if specified)
            if avatar_source:
                if avatar_source not in existing_inputs:
                    self.obs._call(
                        "create_input",
                        scene_name,
                        avatar_source,
                        "ffmpeg_source",
                        {
                            "is_local_file": True,
                            "looping": True,
                            "restart_on_activate": True,
                            "clear_on_media_end": False,
                        },
                        True,
                    )
                    created.append(f"Avatar '{avatar_source}'")
                else:
                    self.obs._ensure_in_scene(scene_name, avatar_source)

            # 4. Banner overlay image source (top layer)
            if banner_source:
                if banner_source not in existing_inputs:
                    self.obs._call(
                        "create_input",
                        scene_name,
                        banner_source,
                        "image_source",
                        {"file": "", "unload": False},
                        True,
                    )
                    created.append(f"Banner overlay '{banner_source}'")
                else:
                    self.obs._ensure_in_scene(scene_name, banner_source)

            # Fit player & banner to full canvas
            self.obs._fit_to_canvas(scene_name, player_source)
            if banner_source:
                self.obs._fit_to_canvas(scene_name, banner_source)
            if avatar_source:
                self.obs._place_avatar_overlay(scene_name, avatar_source)

            # 5. Enforce deterministic Z-order: player (0) -> avatar (1) -> banner (top)
            layers = [player_source]
            if avatar_source:
                layers.append(avatar_source)
            if banner_source:
                layers.append(banner_source)

            z_ok, z_msg = self.enforce_z_order(scene_name, layers)
            if not z_ok:
                logger.warning("Cảnh báo Z-order: {}", z_msg)

            # Switch to this scene as program scene
            self.obs.set_current_scene(scene_name)

            summary = "Đã thiết lập scene chuẩn LiveFlow."
            if created:
                summary += " Tạo mới: " + ", ".join(created) + "."
            summary += f" ({z_msg})"
            logger.info(summary)
            return True, summary

        except Exception as e:
            err = self.obs._friendly_error(e)
            logger.error("Lỗi thiết lập scene chuẩn OBS: {}", err)
            return False, f"Không thể thiết lập scene chuẩn OBS: {err}"

    def enforce_z_order(self, scene_name: str, layer_order: List[str]) -> Tuple[bool, str]:
        """Order sources in scene from bottom (index 0) to top (index N-1).

        Args:
            scene_name: Target OBS scene.
            layer_order: Ordered list of source names from lowest to highest.

        Returns:
            (success, message) with verification check.
        """
        if not self.obs.is_connected:
            return False, "Chưa kết nối OBS Studio."

        items = self.obs.get_scene_item_list(scene_name)
        if not items:
            return False, f"Scene '{scene_name}' không có item nào hoặc không thể đọc."

        name_to_id = {item["name"]: item["id"] for item in items if item.get("name") and item.get("id") is not None}

        # Apply indices in order
        for target_idx, source_name in enumerate(layer_order):
            item_id = name_to_id.get(source_name)
            if item_id is None:
                # Try fetching directly
                item_id = self.obs.get_scene_item_id(scene_name, source_name)
            if item_id is not None:
                self.obs.set_scene_item_index(scene_name, item_id, target_idx)

        # Verification step: re-read items and verify ascending order
        updated_items = self.obs.get_scene_item_list(scene_name)
        indices = {
            item["name"]: item.get("index")
            for item in updated_items
            if item.get("name") in layer_order and item.get("index") is not None
        }

        # Verify that for all i < j, index(layers[i]) < index(layers[j])
        ordering_correct = True
        for i in range(len(layer_order) - 1):
            name_a = layer_order[i]
            name_b = layer_order[i + 1]
            idx_a = indices.get(name_a)
            idx_b = indices.get(name_b)
            if idx_a is not None and idx_b is not None:
                if idx_a >= idx_b:
                    ordering_correct = False
                    break

        layer_summary = " → ".join(f"{name} (#{indices.get(name, '?')})" for name in layer_order)
        if ordering_correct:
            return True, f"Z-order chuẩn xác: {layer_summary}"
        else:
            return False, f"Lệch Z-order sau khi gán: {layer_summary}"

    def verify_composition(
        self,
        scene_name: str = DEFAULT_SCENE,
        player_source: str = DEFAULT_PLAYER_SOURCE,
        banner_source: str = DEFAULT_BANNER_SOURCE,
    ) -> Dict[str, Any]:
        """Perform comprehensive check on OBS composition: resolution, fps, scene, sources, z-order, audio."""
        result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "details": {},
        }

        if not self.obs.is_connected:
            result["valid"] = False
            result["errors"].append("OBS Studio chưa kết nối.")
            return result

        # 1. Canvas check
        video = self.obs.get_video_settings()
        bw = video.get("base_width", 0)
        bh = video.get("base_height", 0)
        fps = video.get("fps", 0)
        result["details"]["video"] = video

        if bw != CANVAS_WIDTH or bh != CANVAS_HEIGHT:
            result["valid"] = False
            result["errors"].append(
                f"Kích thước canvas OBS là {bw}×{bh}, không đúng chuẩn dọc 9:16 ({CANVAS_WIDTH}×{CANVAS_HEIGHT})."
            )

        if fps < 25:
            result["warnings"].append(f"FPS OBS ({fps}) thấp hơn khuyến nghị 30fps.")

        # 2. Scene check
        scenes = self.obs.get_scenes()
        result["details"]["scenes"] = scenes
        if scene_name not in scenes:
            result["valid"] = False
            result["errors"].append(f"Scene '{scene_name}' chưa tồn tại trong OBS.")

        # 3. Sources in scene check
        items = self.obs.get_scene_item_list(scene_name)
        item_names = {i["name"] for i in items if i.get("name")}
        result["details"]["scene_items"] = items

        if player_source not in item_names:
            result["valid"] = False
            result["errors"].append(f"Nguồn phát video '{player_source}' chưa có trong scene '{scene_name}'.")

        if banner_source and banner_source not in item_names:
            result["warnings"].append(f"Nguồn banner '{banner_source}' chưa có trong scene '{scene_name}'.")

        # 4. Z-order check
        if player_source in item_names and banner_source in item_names:
            player_idx = next((i["index"] for i in items if i.get("name") == player_source), -1)
            banner_idx = next((i["index"] for i in items if i.get("name") == banner_source), -1)
            if player_idx is not None and banner_idx is not None and banner_idx <= player_idx:
                result["warnings"].append(
                    f"Z-order chưa chuẩn: Banner ({banner_idx}) nằm dưới hoặc bằng Player ({player_idx}). Banner có thể bị che."
                )

        # 5. Audio check on player source
        muted = self.obs.get_input_mute(player_source)
        if muted is True:
            result["valid"] = False
            result["errors"].append(f"Nguồn âm thanh '{player_source}' đang bị TẮT TIẾNG (Muted) trong OBS.")

        vol = self.obs.get_input_volume(player_source)
        result["details"]["volume"] = vol
        if vol.get("mul", 1.0) <= 0.001:
            result["warnings"].append(f"Âm lượng nguồn '{player_source}' bằng 0 (kéo thanh âm lượng lên trong OBS).")

        return result
