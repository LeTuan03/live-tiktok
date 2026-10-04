"""SceneSet model — defines review environments, spatial alignment, perspective, and lighting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass
class SceneSet:
    """Configuration for a specific review studio environment."""

    id: str
    name: str
    category: str  # gia_dung, my_pham, thoi_trang, bep, cong_nghe, da_nang
    background: str  # path to 1080x1920 video loop or still image
    floor_y: int = 1520  # Y coordinate of the floor line where the presenter stands
    anchor_x: int = 540  # X center where presenter stands
    model_height_px: int = 1180  # Presenter height in frame (sets perspective)
    product_zone: Tuple[int, int, int, int] = (120, 1240, 480, 1480)  # (x1, y1, x2, y2) zone for product showcase
    light_temp: str = "5600K"  # Color temperature of set
    light_dir: str = "left-top"  # Direction of key light
    bg_blur: float = 2.5  # Background blur in pixels for depth-of-field
    shadow_blur: int = 24  # Floor shadow blur
    shadow_dy: int = 8  # Floor shadow vertical offset
    shadow_alpha: float = 0.35  # Floor shadow opacity
    grain: float = 0.02  # Cohesive camera grain
    description: str = ""
    is_custom: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SceneSet:
        # Convert tuple if needed
        pz = data.get("product_zone", (120, 1240, 480, 1480))
        if isinstance(pz, list):
            pz = tuple(pz)
        return cls(
            id=data["id"],
            name=data["name"],
            category=data.get("category", "da_nang"),
            background=data.get("background", ""),
            floor_y=int(data.get("floor_y", 1520)),
            anchor_x=int(data.get("anchor_x", 540)),
            model_height_px=int(data.get("model_height_px", 1180)),
            product_zone=pz,
            light_temp=data.get("light_temp", "5600K"),
            light_dir=data.get("light_dir", "left-top"),
            bg_blur=float(data.get("bg_blur", 2.5)),
            shadow_blur=int(data.get("shadow_blur", 24)),
            shadow_dy=int(data.get("shadow_dy", 8)),
            shadow_alpha=float(data.get("shadow_alpha", 0.35)),
            grain=float(data.get("grain", 0.02)),
            description=data.get("description", ""),
            is_custom=bool(data.get("is_custom", False)),
        )
