"""Clip Library — manages 9 cue gesture clips, product-specific clips, and manual approval states."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from app.presenter.base import PresenterClip
from app.services.script_segment_service import VALID_CUES, normalize_cue


DEFAULT_AVATARS_STORAGE = Path("storage/avatars")


CUE_DESCRIPTIONS: Dict[str, str] = {
    "WAVE_GREET": "Vẫy tay mỉm cười chào người xem khi vào phiên LIVE",
    "HOLD_PRODUCT": "Cầm sản phẩm giơ ngang ngực, hướng về phía camera",
    "SHOW_DETAIL": "Chỉ tay vào nhãn mác, thông số hoặc chi tiết sản phẩm",
    "POINT_PRICE": "Chỉ tay vào góc hiển thị bảng giá khuyến mãi sốc",
    "TRY_ON": "Mặc thử quần áo hoặc thao tác sử dụng thử sản phẩm",
    "CTA_POINT_DOWN": "Chỉ tay xuống góc trái dưới màn hình kêu gọi vào giỏ hàng",
    "NOD_LISTEN": "Gật đầu mỉm cười lắng nghe câu hỏi bình luận của khách",
    "SURPRISE": "Ngạc nhiên mắt mở to thích thú trước ưu đãi deal sốc",
    "IDLE_TALK": "Đứng thuyết minh tự nhiên, cử chỉ nhẹ nhàng nhìn camera",
}


class PresenterClipLibrary:
    """Manages stored gesture clips for presenter identities and enforces manual approval."""

    def __init__(self, root_dir: Optional[Path | str] = None) -> None:
        self.root_dir = Path(root_dir or DEFAULT_AVATARS_STORAGE)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _get_clips_dir(self, avatar_id: int | str) -> Path:
        p = self.root_dir / str(avatar_id) / "clips"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _get_meta_path(self, avatar_id: int | str) -> Path:
        return self._get_clips_dir(avatar_id) / "clips_meta.json"

    def _load_meta(self, avatar_id: int | str) -> Dict[str, Dict]:
        meta_file = self._get_meta_path(avatar_id)
        if not meta_file.exists():
            return {}
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Lỗi đọc metadata clip thư viện avatar {}: {}", avatar_id, e)
            return {}

    def _save_meta(self, avatar_id: int | str, data: Dict[str, Dict]) -> None:
        meta_file = self._get_meta_path(avatar_id)
        try:
            meta_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("Lỗi ghi metadata clip thư viện avatar {}: {}", avatar_id, e)

    def list_clips(self, avatar_id: int | str) -> List[PresenterClip]:
        """List all registered gesture and product clips for the avatar."""
        meta = self._load_meta(avatar_id)
        result: List[PresenterClip] = []
        for key, item in meta.items():
            result.append(PresenterClip.from_dict(item))
        return result

    def get_clip_for_cue(
        self,
        avatar_id: int | str,
        cue: str,
        product_id: Optional[int] = None,
        require_approved: bool = True,
    ) -> Optional[PresenterClip]:
        """Find the best matching clip for a cue.
        
        Priority:
        1. Product-specific approved clip (if product_id given)
        2. Generic approved clip for that specific cue
        3. Generic approved IDLE_TALK clip
        4. Any available approved clip
        """
        norm_cue = normalize_cue(cue)
        meta = self._load_meta(avatar_id)
        all_clips = [PresenterClip.from_dict(v) for v in meta.values()]

        # 1. Product-specific clip
        if product_id is not None:
            for c in all_clips:
                if c.has_product and c.product_id == product_id and c.cue == norm_cue:
                    if (not require_approved or c.is_approved) and Path(c.file_path).exists():
                        return c

        # 2. Specific cue clip
        for c in all_clips:
            if not c.has_product and c.cue == norm_cue:
                if (not require_approved or c.is_approved) and Path(c.file_path).exists():
                    return c

        # 3. Fallback to IDLE_TALK
        if norm_cue != "IDLE_TALK":
            for c in all_clips:
                if not c.has_product and c.cue == "IDLE_TALK":
                    if (not require_approved or c.is_approved) and Path(c.file_path).exists():
                        return c

        # 4. Fallback to any approved clip
        for c in all_clips:
            if (not require_approved or c.is_approved) and Path(c.file_path).exists():
                return c

        return None

    def register_clip(
        self,
        avatar_id: int | str,
        cue: str,
        file_path: Path | str,
        is_approved: bool = False,
        has_product: bool = False,
        product_id: Optional[int] = None,
        duration: float = 0.0,
        notes: Optional[str] = None,
    ) -> PresenterClip:
        """Register or update a clip in the avatar library."""
        norm_cue = normalize_cue(cue)
        meta = self._load_meta(avatar_id)

        key = f"{norm_cue}_prod_{product_id}" if has_product and product_id else norm_cue
        clip = PresenterClip(
            cue=norm_cue,
            file_path=str(file_path),
            duration=duration,
            has_product=has_product,
            product_id=product_id,
            is_approved=is_approved,
            notes=notes or CUE_DESCRIPTIONS.get(norm_cue, ""),
        )
        meta[key] = clip.to_dict()
        self._save_meta(avatar_id, meta)
        logger.info("Đã đăng ký clip cử chỉ avatar {} [cue={}]: {}", avatar_id, norm_cue, file_path)
        return clip

    def set_approval(
        self,
        avatar_id: int | str,
        cue: str,
        is_approved: bool,
        product_id: Optional[int] = None,
    ) -> bool:
        """Mark a clip as manually approved or rejected."""
        norm_cue = normalize_cue(cue)
        meta = self._load_meta(avatar_id)
        key = f"{norm_cue}_prod_{product_id}" if product_id else norm_cue

        if key in meta:
            meta[key]["is_approved"] = is_approved
            self._save_meta(avatar_id, meta)
            logger.info("Cập nhật phê duyệt clip {} -> is_approved={}", key, is_approved)
            return True
        return False
