"""Script Service for AI generation and managing scripts."""

from __future__ import annotations

import math
from typing import Optional, Sequence
from loguru import logger
from app.database.models.product import Product
from app.database.models.script import Script, ScriptVersion
from app.database.repositories.script_repo import ScriptRepository
from app.integrations.llm.openai_provider import OpenAILLMProvider


class ScriptService:
    """Service layer for AI script generation and prompt handling."""

    def __init__(self, script_repo: ScriptRepository) -> None:
        self.script_repo = script_repo

    def build_script_prompt(
        self,
        product: Product,
        style: str = "consultant",
        target_audience: str = "Tất cả khách hàng",
        speaking_speed: str = "normal",
        honorific: str = "Anh/Chị",
        cta_text: Optional[str] = None,
        duration_minutes: float = 2.0,
    ) -> tuple[str, str]:
        """Construct system prompt and user prompt for livestream script generation."""
        system_prompt = (
            "Bạn là một Chuyên gia Kịch bản Livestream Bán hàng chuyên nghiệp trên TikTok/Facebook Live. "
            "Nhiệm vụ của bạn là viết một bài nói bán hàng (script) vô cùng thu hút, thuyết phục và chốt đơn tự nhiên. "
            "Cấu trúc bài nói gồm 4 phần chính:\n"
            "1. Bắt đầu/Mở đầu (Hook) gây chú ý lập tức.\n"
            "2. Nêu bật Nỗi đau/Nhu cầu của khách hàng.\n"
            "3. Giới thiệu sản phẩm & Các điểm vượt trội (USP), ưu đãi khủng.\n"
            "4. Kêu gọi hành động (CTA) chốt đơn ngay lập tức.\n"
            "Văn phong tự nhiên, hào hứng, nói trực tiếp với người xem."
        )

        speed_note = {
            "slow": "Nói chậm rãi, nắn nót từng lời",
            "normal": "Tốc độ vừa phải, năng lượng",
            "fast": "Tốc độ nhanh, dồn dập kích thích mua hàng",
        }.get(speaking_speed, "Vừa phải")

        style_note = {
            "friendly": "Thân thiện, gần gũi như người nhà",
            "consultant": "Chuyên gia tư vấn sâu sắc, đáng tin cậy",
            "energetic": "Năng lượng bùng nổ, dồn dập, cuồng nhiệt",
            "concise": "Ngắn gọn, súc tích, đi thẳng vào giá trị & ưu đãi",
        }.get(style, style)

        details = f"- Tên sản phẩm: {product.name}\n"
        if product.sku:
            details += f"- Mã sản phẩm/SKU: {product.sku}\n"
        if getattr(product, "price", None) is not None:
            details += f"- Giá bán hiện tại: {product.price:,.0f} VNĐ\n"
        if getattr(product, "promo_price", None) is not None:
            details += f"- Giá khuyến mãi: {product.promo_price:,.0f} VNĐ\n"
        if getattr(product, "description", None):
            details += f"- Mô tả/Giới thiệu: {product.description}\n"
        if getattr(product, "verified_features", None):
            details += f"- Tính năng xác minh (USP): {product.verified_features}\n"
        if getattr(product, "ingredients", None):
            details += f"- Thành phần/Thông số: {product.ingredients}\n"

        user_prompt = (
            f"Hãy viết kịch bản livestream bán hàng cho sản phẩm sau:\n\n"
            f"{details}\n"
            f"THÔNG TIN CẤU HÌNH KỊCH BẢN:\n"
            f"- Phong cách nói: {style_note}\n"
            f"- Đối tượng mục tiêu: {target_audience}\n"
            f"- Cách xưng hô với người xem: {honorific}\n"
            f"- Tốc độ nói mong muốn: {speed_note}\n"
            f"- Kêu gọi hành động cụ thể: {cta_text or 'Bấm ngay vào giỏ hàng góc trái màn hình'}\n"
            f"- Thời lượng ước tính: {duration_minutes} phút (khoảng {int(duration_minutes * 160)} từ).\n\n"
            f"YÊU CẦU ĐẦU RA:\n"
            f"- Trả về duy nhất lời thoại livestream chi tiết, không ghi lời dẫn hay chú thích đạo diễn.\n"
            f"- Trực tiếp là lời của streamer đang nói trên live."
        )

        return system_prompt, user_prompt

    async def generate_script_with_ai(
        self,
        product: Product,
        provider: OpenAILLMProvider,
        style: str = "consultant",
        target_audience: str = "Tất cả khách hàng",
        speaking_speed: str = "normal",
        honorific: str = "Anh/Chị",
        cta_text: Optional[str] = None,
        duration_minutes: float = 2.0,
    ) -> Script:
        """Generate a new script using AI provider and save to DB."""
        system_prompt, user_prompt = self.build_script_prompt(
            product=product,
            style=style,
            target_audience=target_audience,
            speaking_speed=speaking_speed,
            honorific=honorific,
            cta_text=cta_text,
            duration_minutes=duration_minutes,
        )

        try:
            content = await provider.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
            )
        except Exception as e:
            logger.warning(f"Không thể kết nối AI Provider ({e}), tạo kịch bản mẫu fallback.")
            content = self._generate_fallback_script(product, style, honorific, cta_text)

        word_count = len(content.split())
        estimated_seconds = (word_count / 150) * 60  # avg 150 words per min

        script = self.script_repo.create(
            product_id=product.id,
            title=f"Kịch bản Livestream - {product.name}",
            content=content,
            style=style,
            target_audience=target_audience,
            language="vi",
            speaking_speed=speaking_speed,
            honorific=honorific,
            cta_text=cta_text,
            duration_seconds=estimated_seconds,
        )
        return script

    def _generate_fallback_script(
        self, product: Product, style: str, honorific: str, cta_text: Optional[str]
    ) -> str:
        price_str = f"{product.price:,.0f}đ" if product.price else "giá cực ưu đãi"
        cta = cta_text or "nhấp ngay vào giỏ hàng bên dưới để chốt đơn ngay"
        return (
            f"Chào mừng tất cả {honorific} đang theo dõi phiên livestream hôm nay! "
            f"Đặc biệt trong phiên live này, bên em xin giới thiệu dòng sản phẩm hot nhất: {product.name}.\n\n"
            f"Sản phẩm này mang lại giải pháp tuyệt vời cho {honorific}. {product.description or ''}\n\n"
            f"Duy nhất hôm nay tại livestream, sản phẩm có giá ưu đãi chỉ {price_str}! "
            f"Số lượng có hạn, {honorific} hãy {cta} trước khi hết suất ưu đãi nhé!"
        )

    def get_scripts_by_product(self, product_id: int) -> Sequence[Script]:
        return self.script_repo.get_by_product(product_id)

    def get_script_versions(self, script_id: int) -> Sequence[ScriptVersion]:
        return self.script_repo.get_versions(script_id)

    def update_script(
        self, script_id: int, content: str, title: Optional[str] = None, note: Optional[str] = None
    ) -> Optional[Script]:
        return self.script_repo.update_content(script_id, content, change_note=note, title=title)

    def delete_script(self, script_id: int) -> bool:
        return self.script_repo.delete(script_id)
