"""AI Director & Script Generator for Google Veo 3 Video Scenes."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.factory import get_llm_provider


class VeoScene(BaseModel):
    """A single scene/shot generated for Veo 3."""

    scene_index: int
    shot_type: str = "DEMO"  # HOOK, FEATURE_DEMO, USP, CTA
    speech_text: str  # Vietnamese spoken text for Edge-TTS
    veo_prompt: str  # Detailed English cinematic prompt for Veo 3
    duration_seconds: int = 5
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    status: str = "PENDING"  # PENDING, GENERATING, COMPLETED, FAILED
    error_message: Optional[str] = None


class VeoShowcaseScript(BaseModel):
    """Complete multi-scene showcase script."""

    title: str
    product_id: Optional[int] = None
    avatar_id: Optional[int] = None
    scenes: List[VeoScene] = Field(default_factory=list)


class VeoScriptService:
    """Service to automatically generate structured scenes for Veo 3."""

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None) -> None:
        self.llm = llm_provider or get_llm_provider()

    async def generate_showcase_script(
        self,
        product: Product,
        avatar: Optional[AIAvatar] = None,
        style: str = "energetic",
        num_scenes: int = 3,
    ) -> VeoShowcaseScript:
        """Generate structured scenes (speech + Veo prompt) for a product."""
        avatar_desc = self._get_avatar_description(avatar)
        product_info = self._get_product_summary(product)

        system_prompt = (
            "Bạn là Đạo Diễn Livestream & Video AI chuyên nghiệp (AI Video Director). "
            "Nhiệm vụ của bạn là nhận thông tin sản phẩm và người mẫu AI, sau đó viết kịch bản "
            f"gồm đúng {num_scenes} phân cảnh (scenes) liên kết chặt chẽ để tạo thành 1 video bán hàng hoàn chỉnh.\n\n"
            "YÊU CẦU CHO MỖI PHÂN CẢNH:\n"
            "1. `shot_type`: Phân loại cảnh (HOOK, FEATURE_DEMO, USP, CTA).\n"
            "2. `speech_text`: Câu thoại tiếng Việt tự nhiên, năng lượng, đúng phong cách livestream bán hàng Việt Nam (ngắn gọn, khoảng 12-16 từ, đọc trong 4.5-5 giây).\n"
            "3. `veo_prompt`: Prompt bằng TIẾNG ANH chi tiết gửi cho Google Veo 3 để tạo video có chuyển động chân thực. Phải miêu tả rõ:\n"
            "   - Chủ thể: người mẫu cử động cơ thể (vẫy tay, cười, gật đầu, thay đổi tư thế).\n"
            "   - Tương tác với sản phẩm: cầm sản phẩm trên tay, xoay sản phẩm, chỉ vào chi tiết.\n"
            "   - Góc máy và ánh sáng: 'Vertical 9:16 cinematic video, modern livestream studio lighting, natural hand and body movement, photorealistic, 4k'.\n"
            "4. `duration_seconds`: Mặc định là 5 giây cho mỗi cảnh.\n\n"
            "ĐỊNH DẠNG ĐẦU RA BẮT BUỘC (JSON thuần, không bọc markdown rườm rà):\n"
            "{\n"
            '  "title": "Tiêu đề video",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "scene_index": 1,\n'
            '      "shot_type": "HOOK",\n'
            '      "speech_text": "...",\n'
            '      "veo_prompt": "...",\n'
            '      "duration_seconds": 5\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        user_prompt = (
            f"THÔNG TIN NGƯỜI MẪU AI:\n{avatar_desc}\n\n"
            f"THÔNG TIN SẢN PHẨM:\n{product_info}\n\n"
            f"PHONG CÁCH: {style} (nhiệt tình, cuốn hút, chốt đơn nhanh)\n"
            f"HÃY TẠO ĐÚNG {num_scenes} PHÂN CẢNH THEO ĐỊNH DẠNG JSON YÊU CẦU."
        )

        try:
            raw_text = await self.llm.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                # Mỗi phân cảnh tốn ~450-550 token (thoại tiếng Việt + veo_prompt tiếng Anh).
                # Để quá thấp thì JSON bị cắt giữa chừng -> parse hỏng -> rơi về template chết.
                max_tokens=max(2500, 900 * num_scenes + 1500),
            )
            parsed = self._parse_json_response(raw_text, product, num_scenes)
            if parsed and parsed.scenes:
                parsed.product_id = product.id
                if avatar:
                    parsed.avatar_id = avatar.id
                return parsed

            # Parse hỏng: ghi rõ lý do thay vì im lặng dùng template.
            tail = (raw_text or "")[-160:].replace("\n", " ")
            logger.error(
                "Không đọc được JSON kịch bản Veo (độ dài {} ký tự, có thể bị cắt). "
                "Đang dùng template dự phòng — kịch bản sẽ CHUNG CHUNG, không bám sản phẩm. "
                "Đuôi phản hồi: ...{}",
                len(raw_text or ""),
                tail,
            )
        except Exception as e:
            logger.error(
                "Lỗi gọi LLM sinh kịch bản Veo ({}), chuyển sang template dự phòng — "
                "kịch bản sẽ CHUNG CHUNG, không bám sản phẩm.",
                e,
            )

        # Fallback template
        return self._create_fallback_script(product, avatar, num_scenes)

    def _get_avatar_description(self, avatar: Optional[AIAvatar]) -> str:
        if not avatar:
            return "Attractive Vietnamese female presenter, professional livestream host, wearing smart casual attire."
        gender = getattr(avatar, "gender", "female")
        name = getattr(avatar, "name", "MC")
        gender_word = "female" if gender == "female" else "male"
        return f"Vietnamese {gender_word} presenter named {name}, professional livestream host with warm and energetic expression."

    def _get_product_summary(self, product: Product) -> str:
        name = product.name or "Sản phẩm"
        price = f"{product.price:,.0f} VNĐ" if product.price else "Giá ưu đãi"
        promo = f"{product.promo_price:,.0f} VNĐ" if product.promo_price else ""
        desc = product.description or ""
        features = getattr(product, "key_features", "") or ""

        info = f"- Tên sản phẩm: {name}\n- Giá bán: {price}"
        if promo:
            info += f"\n- Giá khuyến mãi: {promo}"
        if desc:
            info += f"\n- Mô tả: {desc[:200]}"
        if features:
            info += f"\n- Tính năng nổi bật: {features[:200]}"
        return info

    @staticmethod
    def _salvage_truncated_json(text: str) -> str:
        """Vá JSON bị cắt giữa chừng bằng cách giữ lại các phân cảnh đã hoàn chỉnh.

        LLM đôi khi chạm trần token và dừng giữa một phân cảnh. Thay vì vứt cả phản hồi
        (rồi rơi về template chung chung), ta cắt tại phân cảnh hoàn chỉnh cuối cùng
        rồi đóng lại mảng/đối tượng cho hợp lệ.
        """
        if not text:
            return ""
        start = text.find('"scenes"')
        if start < 0:
            return ""
        bracket = text.find("[", start)
        if bracket < 0:
            return ""

        depth = 0
        in_str = False
        escaped = False
        last_complete = -1

        for i in range(bracket + 1, len(text)):
            ch = text[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_complete = i
            elif ch == "]" and depth == 0:
                # Mảng đã đóng đúng cách — không cần vá.
                return ""

        if last_complete < 0:
            return ""
        return text[: last_complete + 1] + "]}"

    def _parse_json_response(
        self, raw_text: str, product: Product, num_scenes: int
    ) -> Optional[VeoShowcaseScript]:
        clean = raw_text.strip()
        # Find ```json ... ``` or ``` ... ```
        block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
        if block_match:
            clean = block_match.group(1).strip()
        else:
            brace_match = re.search(r"\{[\s\S]*\}", clean)
            if brace_match:
                clean = brace_match.group(0).strip()

        # Remove trailing commas before closing braces/brackets if any
        clean_sanitized = re.sub(r",\s*([\}\]])", r"\1", clean)

        data = None
        for candidate in [clean_sanitized, clean, self._salvage_truncated_json(clean)]:
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    break
            except Exception:
                continue

        if not isinstance(data, dict):
            return None

        scenes_data = data.get("scenes", [])
        scenes: List[VeoScene] = []
        for i, s in enumerate(scenes_data, start=1):
            scene = VeoScene(
                scene_index=i,
                shot_type=s.get("shot_type", "DEMO"),
                speech_text=s.get("speech_text", "").strip(),
                veo_prompt=s.get("veo_prompt", "").strip(),
                duration_seconds=int(s.get("duration_seconds", 5)),
            )
            if scene.speech_text and scene.veo_prompt:
                scenes.append(scene)

        if len(scenes) >= 2:
            return VeoShowcaseScript(
                title=data.get("title", f"Video giới thiệu {product.name}"),
                scenes=scenes[:num_scenes],
            )
        return None

    def _create_fallback_script(
        self, product: Product, avatar: Optional[AIAvatar], num_scenes: int
    ) -> VeoShowcaseScript:
        """Create high quality fallback scenes if LLM is unavailable."""
        name = product.name or "Sản phẩm"
        gender_word = "female" if not avatar or getattr(avatar, "gender", "female") == "female" else "male"

        s1 = VeoScene(
            scene_index=1,
            shot_type="HOOK",
            speech_text=f"Dạ em chào cả nhà! Dừng lại xem ngay siêu phẩm {name} cực xịn này nha!",
            veo_prompt=(
                f"Vertical 9:16 cinematic video of an attractive Vietnamese {gender_word} livestream host, "
                "standing in a modern brightly lit live studio, enthusiastically smiling and waving at camera while holding the product, "
                "natural dynamic body posture and eye contact, photorealistic 4k."
            ),
            duration_seconds=5,
        )

        s2 = VeoScene(
            scene_index=2,
            shot_type="FEATURE_DEMO",
            speech_text=f"Thiết kế tinh xảo, độ bền cao, tính năng thông minh vượt trội phục vụ trọn vẹn nhu cầu của anh chị!",
            veo_prompt=(
                f"Close-up vertical 9:16 shot, Vietnamese {gender_word} host's hands carefully holding, turning, "
                "and demonstrating the product details, pointing out features with smooth natural hand movements, "
                "soft studio focus, clean professional aesthetic, 4k."
            ),
            duration_seconds=5,
        )

        s3 = VeoScene(
            scene_index=3,
            shot_type="CTA",
            speech_text=f"Ưu đãi sốc duy nhất hôm nay, anh chị nhấp ngay vào giỏ hàng góc trái để nhận deal hời nhé!",
            veo_prompt=(
                f"Medium vertical 9:16 shot, Vietnamese {gender_word} host gesturing warmly towards the bottom corner of the frame, "
                "holding the product and encouraging viewers with an engaging happy smile, natural head nods, "
                "vibrant modern live studio lighting, 4k."
            ),
            duration_seconds=5,
        )

        scenes = [s1, s2, s3][:num_scenes]
        return VeoShowcaseScript(
            title=f"Video giới thiệu {name}",
            product_id=product.id,
            avatar_id=avatar.id if avatar else None,
            scenes=scenes,
        )
