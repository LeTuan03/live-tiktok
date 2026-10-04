"""Built-in library of review environments (SceneSets) and set management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger
from PIL import Image, ImageDraw

from app.sets.scene_set import SceneSet


DEFAULT_SETS_DIR = Path("storage/sets")


def generate_backdrop_image(
    output_path: Path | str,
    bg_style: str = "studio_gradient",
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Generate high-resolution aesthetic 1080x1920 studio backdrop image using Pillow."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if bg_style == "phong_khach_am":
        # Warm ambient living room tones: dark warm browns, amber highlights
        base_color = (32, 26, 22)
        accent1 = (55, 42, 34)
        accent2 = (80, 58, 42)
        glow_color = (255, 180, 100, 30)
    elif bg_style == "bep_sang":
        # Bright warm modern kitchen: clean light grays with warm morning sunlight
        base_color = (40, 44, 48)
        accent1 = (60, 66, 72)
        accent2 = (85, 92, 98)
        glow_color = (255, 230, 160, 35)
    elif bg_style == "quay_my_pham_trang":
        # Minimalist cosmetic counter: clean soft studio lighting, delicate blush tints
        base_color = (35, 33, 40)
        accent1 = (52, 48, 62)
        accent2 = (75, 70, 88)
        glow_color = (240, 210, 230, 30)
    elif bg_style == "studio_den_neon":
        # Sleek dark cyber/tech studio: deep indigo, neon violet and cyan glow
        base_color = (12, 14, 24)
        accent1 = (20, 24, 45)
        accent2 = (30, 36, 68)
        glow_color = (140, 40, 255, 45)
    elif bg_style == "shop_thoi_trang":
        # Chic fashion boutique: charcoal with warm spotlights
        base_color = (28, 25, 26)
        accent1 = (45, 40, 42)
        accent2 = (68, 60, 62)
        glow_color = (255, 200, 140, 30)
    else:  # studio_gradient
        # Modern neutral studio gradient: deep slate with soft rim glow
        base_color = (18, 22, 30)
        accent1 = (30, 38, 52)
        accent2 = (45, 56, 78)
        glow_color = (90, 140, 255, 35)

    img = Image.new("RGBA", (width, height), base_color + (255,))
    draw = ImageDraw.Draw(img, "RGBA")

    # Draw gradient-like ambient illumination
    # Top ambient light
    draw.ellipse((width // 4, -150, width * 3 // 4, height // 3), fill=accent2 + (180,))
    draw.ellipse((width // 6, height // 4, width * 5 // 6, height * 3 // 4), fill=accent1 + (150,))

    # Soft glowing spotlight
    draw.ellipse((width // 3 - 50, height // 3 - 50, width * 2 // 3 + 50, height * 2 // 3 + 50), fill=glow_color)

    # Floor boundary line and floor shading
    floor_y = 1520
    draw.line([(0, floor_y), (width, floor_y)], fill=(255, 255, 255, 25), width=2)
    # Floor darker gradient
    draw.rectangle([(0, floor_y), (width, height)], fill=(12, 14, 18, 120))

    # Product showcase stand silhouette
    table_rect = (100, 1260, 480, 1520)
    draw.rounded_rectangle(table_rect, radius=20, fill=(35, 40, 52, 220), outline=(255, 255, 255, 30), width=1)

    # Convert to RGB and save
    final_img = img.convert("RGB")
    final_img.save(str(target), format="JPEG", quality=95)
    return target


BUILTIN_SETS: List[SceneSet] = [
    SceneSet(
        id="phong_khach_am",
        name="Phòng khách ấm áp (Gia dụng & Decor)",
        category="gia_dung",
        background="storage/sets/phong_khach_am.jpg",
        floor_y=1520,
        anchor_x=540,
        model_height_px=1180,
        product_zone=(100, 1240, 480, 1500),
        light_temp="3200K",
        light_dir="left-top",
        bg_blur=2.5,
        shadow_blur=24,
        shadow_dy=8,
        shadow_alpha=0.35,
        description="Không gian phòng khách ấm cúng, sofa gỗ và ánh sáng tự nhiên. Tối ưu cho nồi chiên, máy lọc không khí, robot hút bụi.",
    ),
    SceneSet(
        id="bep_sang",
        name="Bếp hiện đại nắng sớm (Đồ bếp & Thực phẩm)",
        category="bep",
        background="storage/sets/bep_sang.jpg",
        floor_y=1540,
        anchor_x=520,
        model_height_px=1160,
        product_zone=(120, 1260, 500, 1520),
        light_temp="5000K",
        light_dir="top",
        bg_blur=3.0,
        shadow_blur=22,
        shadow_dy=10,
        shadow_alpha=0.38,
        description="Bàn đảo bếp mặt đá sang trọng, ánh nắng qua khung cửa sổ. Thích hợp cho chảo chống dính, nồi cơm điện, dao kéo.",
    ),
    SceneSet(
        id="quay_my_pham_trang",
        name="Quầy mỹ phẩm tối giản (Skincare & Mỹ phẩm)",
        category="my_pham",
        background="storage/sets/quay_my_pham_trang.jpg",
        floor_y=1500,
        anchor_x=540,
        model_height_px=1180,
        product_zone=(100, 1220, 460, 1480),
        light_temp="5600K",
        light_dir="front",
        bg_blur=3.2,
        shadow_blur=20,
        shadow_dy=6,
        shadow_alpha=0.30,
        description="Bàn gương trắng tinh khôi, hoa tươi và ánh sáng đánh đều làm nổi bật làn da và chai lọ mỹ phẩm cao cấp.",
    ),
    SceneSet(
        id="studio_den_neon",
        name="Studio viền Neon (Thời trang & Công nghệ)",
        category="cong_nghe",
        background="storage/sets/studio_den_neon.jpg",
        floor_y=1520,
        anchor_x=540,
        model_height_px=1200,
        product_zone=(100, 1240, 480, 1500),
        light_temp="6500K",
        light_dir="rim",
        bg_blur=2.0,
        shadow_blur=26,
        shadow_dy=8,
        shadow_alpha=0.45,
        description="Nền tối hiện đại với đèn viền LED tím/xanh phong cách gaming, tai nghe bluetooth, đồng hồ thông minh, phụ kiện.",
    ),
    SceneSet(
        id="shop_thoi_trang",
        name="Cửa hàng thời trang boutique (Quần áo & Phụ kiện)",
        category="thoi_trang",
        background="storage/sets/shop_thoi_trang.jpg",
        floor_y=1550,
        anchor_x=540,
        model_height_px=1220,
        product_zone=(120, 1260, 480, 1520),
        light_temp="4000K",
        light_dir="top",
        bg_blur=2.8,
        shadow_blur=24,
        shadow_dy=9,
        shadow_alpha=0.35,
        description="Giá treo quần áo boutique, gương lớn và đèn spotlight thời trang tôn vinh form dáng sản phẩm.",
    ),
    SceneSet(
        id="studio_gradient",
        name="Studio Gradient đa năng (Dự phòng cho mọi ngành)",
        category="da_nang",
        background="storage/sets/studio_gradient.jpg",
        floor_y=1520,
        anchor_x=540,
        model_height_px=1180,
        product_zone=(100, 1240, 480, 1500),
        light_temp="5600K",
        light_dir="front",
        bg_blur=2.5,
        shadow_blur=24,
        shadow_dy=8,
        shadow_alpha=0.35,
        description="Phông nền studio chuyển sắc chuẩn truyền hình, sạch sẽ, tinh tế, ứng dụng tốt cho tất cả các ngành hàng.",
    ),
]


class SceneSetLibrary:
    """Manages available review studio environments and custom scene sets."""

    def __init__(self, storage_dir: Optional[Path | str] = None) -> None:
        self.storage_dir = Path(storage_dir or DEFAULT_SETS_DIR)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.custom_file = self.storage_dir / "custom_sets.json"
        self._ensure_backdrop_files()

    def _ensure_backdrop_files(self) -> None:
        """Auto-generate default backdrop images if they do not yet exist on disk."""
        for s in BUILTIN_SETS:
            bg_path = Path(s.background)
            if not bg_path.exists():
                try:
                    generate_backdrop_image(bg_path, bg_style=s.id)
                except Exception as e:
                    logger.debug("Không thể sinh ảnh nền cho set {}: {}", s.id, e)

    def list_sets(self) -> List[SceneSet]:
        """Return all built-in and user-customized scene sets."""
        result = list(BUILTIN_SETS)
        if self.custom_file.exists():
            try:
                data = json.loads(self.custom_file.read_text(encoding="utf-8"))
                for item in data:
                    result.append(SceneSet.from_dict(item))
            except Exception as e:
                logger.error("Lỗi đọc custom sets: {}", e)
        return result

    def get_all(self) -> List[SceneSet]:
        """Alias for list_sets()."""
        return self.list_sets()

    def get_set(self, set_id: str) -> Optional[SceneSet]:
        """Find a SceneSet by its ID."""
        for s in self.list_sets():
            if s.id == set_id:
                return s
        return None

    def get_set_or_default(self, set_id: str) -> SceneSet:
        """Find a SceneSet by ID or fallback to standard gradient studio."""
        found = self.get_set(set_id)
        if found:
            return found
        return self.get_set("studio_gradient") or BUILTIN_SETS[0]

    def get_default_for_category(self, category: str) -> SceneSet:
        """Get best matching scene set for a product category."""
        cat_lower = (category or "").lower()
        if any(k in cat_lower for k in ("bếp", "bep", "nồi", "chảo", "nướng", "thực phẩm", "food")):
            return self.get_set("bep_sang") or BUILTIN_SETS[1]
        if any(k in cat_lower for k in ("mỹ phẩm", "my pham", "skincare", "kem", "son", "da", "dưỡng")):
            return self.get_set("quay_my_pham_trang") or BUILTIN_SETS[2]
        if any(k in cat_lower for k in ("thời trang", "thoi trang", "áo", "quần", "váy", "đầm", "giày")):
            return self.get_set("shop_thoi_trang") or BUILTIN_SETS[4]
        if any(k in cat_lower for k in ("công nghệ", "cong nghe", "tai nghe", "điện thoại", "loa", "phụ kiện")):
            return self.get_set("studio_den_neon") or BUILTIN_SETS[3]
        if any(k in cat_lower for k in ("gia dụng", "gia dung", "nhà cửa", "đời sống", "decor", "quạt")):
            return self.get_set("phong_khach_am") or BUILTIN_SETS[0]

        # Default fallback
        return self.get_set("phong_khach_am") or BUILTIN_SETS[0]

    def save_custom_set(self, scene_set: SceneSet) -> None:
        """Add or update a custom SceneSet."""
        scene_set.is_custom = True
        custom_sets = []
        if self.custom_file.exists():
            try:
                custom_sets = json.loads(self.custom_file.read_text(encoding="utf-8"))
            except Exception:
                custom_sets = []

        # Remove existing if same ID
        custom_sets = [s for s in custom_sets if s.get("id") != scene_set.id]
        custom_sets.append(scene_set.to_dict())

        self.custom_file.write_text(json.dumps(custom_sets, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Đã lưu bối cảnh: {}", scene_set.name)
