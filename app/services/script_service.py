"""Script Service for AI generation, customization, and managing scripts."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Sequence
from loguru import logger
from app.database.models.product import Product
from app.database.models.script import Script, ScriptVersion
from app.database.repositories.script_repo import ScriptRepository
from app.integrations.llm.base import BaseLLMProvider


class ScriptService:
    """Service layer for AI script generation, suggestions, and prompt handling."""

    def __init__(self, script_repo: ScriptRepository) -> None:
        self.script_repo = script_repo

    def build_script_prompt(
        self,
        product: Optional[Product] = None,
        product_name: str = "",
        price: Optional[float] = None,
        promo_price: Optional[float] = None,
        description: str = "",
        verified_features: str = "",
        ingredients: str = "",
        script_type: str = "full_live",
        style: str = "energetic",
        target_audience: str = "Tất cả khách hàng",
        speaking_speed: str = "normal",
        honorific: str = "Anh/Chị",
        promotions_deal: Optional[str] = None,
        key_highlights: Optional[str] = None,
        cta_text: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        duration_minutes: float = 2.0,
    ) -> tuple[str, str]:
        """Construct system prompt and user prompt for livestream script generation."""
        # Resolve product fields from product if passed
        name = product.name if product else (product_name or "Sản phẩm")
        sku = (product.sku if product else "") or ""
        p_price = (product.price if product and product.price is not None else price)
        p_promo = (product.promo_price if product and product.promo_price is not None else promo_price)
        p_desc = (product.description if product and product.description else description) or ""
        p_feat = (product.verified_features if product and product.verified_features else verified_features) or ""
        p_ingr = (product.ingredients if product and product.ingredients else ingredients) or ""

        script_type_instructions = {
            "full_live": (
                "Cấu trúc Kịch bản Livestream Bán Hàng Toàn Diện (4 phần chuẩn TikTok Live):\n"
                "1. Hook mở đầu (15s): Câu giật gân, chặn ngón tay người lướt live, đánh trúng tò mò.\n"
                "2. Chạm nỗi đau (20s): Nêu vấn đề khách đang gặp phải khiến họ thấy mình trong đó.\n"
                "3. Trình diễn USP & Trải nghiệm thực tế (45s): Giải pháp vượt trội, điểm khác biệt đắt giá của sản phẩm.\n"
                "4. Tung Deal độc quyền & Chốt đơn (40s): Đẩy cảm xúc, đếm ngược thời gian và kêu gọi đặt hàng ngay."
            ),
            "flash_sale": (
                "Cấu trúc Kịch bản Flash Sale Chớp Nhoáng (60-90 giây dồn dập):\n"
                "- Nhịp điệu cực nhanh, hào hứng, tạo áp lực thời gian.\n"
                "- Tuyên bố deal sốc chưa từng có chỉ diễn ra trong ít phút.\n"
                "- Đếm ngược số lượng giỏ hàng còn lại (ví dụ chỉ còn 10-15 suất).\n"
                "- Thôi thúc người xem nhấn giỏ hàng thanh toán ngay lập tức."
            ),
            "storytelling": (
                "Cấu trúc Kịch bản Kể Chuyện / Review Trải Nghiệm Thực Tế:\n"
                "- Bắt đầu bằng một câu chuyện hoặc tình huống éo le mà streamer hoặc khách hàng đã từng trải qua.\n"
                "- Hành trình tìm ra và trải nghiệm sản phẩm, sự thay đổi bất ngờ sau khi dùng.\n"
                "- Chia sẻ chân thành, không gượng gạo, tạo sự đồng cảm và tin tưởng tuyệt đối.\n"
                "- Kêu gọi người xem cùng trải nghiệm giải pháp này với mức giá ưu đãi hôm nay."
            ),
            "objection_handling": (
                "Cấu trúc Kịch bản Bẻ Gãy Lời Từ Chối & Nghi Ngại:\n"
                "- Trực tiếp giải quyết các thắc mắc phổ biến: 'sao đắt thế?', 'dùng có hiệu quả thật không?', 'hàng chính hãng không?'.\n"
                "- So sánh trực quan giữa sản phẩm này với các loại trôi nổi rẻ tiền trên thị trường.\n"
                "- Nêu rõ chính sách bảo hành/đổi trả/kiểm hàng NẾU có trong dữ liệu; nếu không có thì mời khách hỏi shop.\n"
                "- Chốt hạ bằng ưu đãi đặc quyền trong live."
            ),
            "giveaway_hook": (
                "Cấu trúc Kịch bản Minigame / Kéo Tương Tác & Giữ Mắt Xem:\n"
                "- Công bố quà tặng tri ân hoặc giá sốc 0 đồng / 1k cho người xem tương tác nhiều nhất.\n"
                "- Hướng dẫn thể lệ: Thả đủ tim, chia sẻ live, hoặc comment số may mắn vào ô chat.\n"
                "- Khéo léo lồng ghép giới thiệu sản phẩm chính trong lúc chờ quay thưởng.\n"
                "- Giữ chân người xem ở lại lâu và sẵn sàng cho đợt chốt đơn tiếp theo."
            ),
        }.get(script_type, "Kịch bản bán hàng livestream thực chiến, tối ưu chốt đơn.")

        style_note = {
            "energetic": "Năng lượng bùng nổ, dồn dập, nhiệt huyết cao độ, kích thích cảm xúc mua hàng",
            "consultant": "Chuyên gia tư vấn sâu sắc, đáng tin cậy, phân tích chi tiết, tạo niềm tin",
            "friendly": "Thân thiện, gần gũi, tâm tình như chị em/bạn thân thủ thỉ với nhau",
            "humorous": "Hài hước, dí dỏm, bắt trend TikTok vui nhộn, tạo không khí sảng khoái",
            "luxury": "Sang trọng, đẳng cấp, ngôn từ tinh tế, tập trung vào giá trị trải nghiệm cao cấp",
            "concise": "Ngắn gọn, súc tích, đi thẳng vào giá trị cốt lõi và ưu đãi",
        }.get(style, style)

        speed_note = {
            "slow": "Nói chậm rãi, nắn nót, giải thích rõ ràng từng ý",
            "normal": "Tốc độ vừa phải, tự nhiên, nhịp nhàng",
            "fast": "Tốc độ nhanh, dồn dập, tạo cảm giác hối hả khẩn cấp",
        }.get(speaking_speed, "Vừa phải, tự nhiên")

        system_prompt = (
            "Bạn là một Bậc Thầy Kịch Bản Livestream Bán Hàng trên TikTok và Facebook Live số 1 Việt Nam. "
            "Nhiệm vụ của bạn là viết một bài nói bán hàng (script) có tính thôi miên, cuốn hút từng giây, "
            "giữ chân người xem từ đầu đến cuối và chốt đơn tự nhiên, hiệu quả cao.\n\n"
            f"ĐỊNH HƯỚNG LOẠI KỊCH BẢN:\n{script_type_instructions}\n\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. Chỉ trả về duy nhất lời thoại mà streamer sẽ nói trực tiếp trên livestream.\n"
            "2. KHÔNG chèn gợi ý hành động/biểu cảm vào trong lời nói; lời thoại phải hoàn toàn sạch để bộ máy đọc TTS không đọc nhầm.\n"
            "3. Không đưa lời chào hỏi rườm rà hay mở đầu kiểu 'Dưới đây là kịch bản...'. Vào thẳng câu mở đầu phiên live luôn!\n"
            "4. Văn phong tiếng Việt tự nhiên, chuẩn ngôn ngữ livestream thương mại điện tử hiện đại.\n"
            "5. CHỈ dùng thông tin sản phẩm được cung cấp bên dưới. KHÔNG bịa thêm công dụng, chứng nhận, "
            "số liệu, giải thưởng, tồn kho, chương trình khuyến mãi hay chính sách ship/bảo hành/đổi trả "
            "nếu không có trong dữ liệu. Không hứa hẹn kết quả về sức khỏe, làm đẹp, tài chính.\n"
            "6. Nếu dữ liệu thiếu, hãy nói chung chung và mời khách hỏi thêm thay vì khẳng định.\n"
            "7. ĐỊNH DẠNG ĐẦU RA: Xuất định dạng JSON phân đoạn chuẩn cấu trúc:\n"
            "{\n"
            '  "set_id": "phong_khach_am",\n'
            '  "segments": [\n'
            '    { "text": "Lời nói của đoạn 1", "cue": "WAVE_GREET", "shot": "WIDE_MODEL", "emphasis": "high" },\n'
            '    { "text": "Lời nói của đoạn 2", "cue": "HOLD_PRODUCT", "shot": "MEDIUM_MODEL", "emphasis": "normal" },\n'
            '    { "text": "Lời nói của đoạn 3", "cue": "SHOW_DETAIL", "shot": "CLOSEUP_PRODUCT", "emphasis": "normal" },\n'
            '    { "text": "Lời nói của đoạn 4", "cue": "POINT_PRICE", "shot": "SPLIT", "emphasis": "high" },\n'
            '    { "text": "Lời nói của đoạn 5", "cue": "CTA_POINT_DOWN", "shot": "MEDIUM_MODEL", "emphasis": "high" }\n'
            "  ]\n"
            "}\n"
            "Các cue: WAVE_GREET, HOLD_PRODUCT, SHOW_DETAIL, POINT_PRICE, TRY_ON, CTA_POINT_DOWN, NOD_LISTEN, SURPRISE, IDLE_TALK.\n"
            "Các shot: WIDE_MODEL, MEDIUM_MODEL, CLOSEUP_PRODUCT, SPLIT, INSET_PRODUCT, TRYON_WIDE."
        )

        details = f"- Tên sản phẩm: {name}\n"
        if sku:
            details += f"- Mã sản phẩm/SKU: {sku}\n"
        if p_price is not None and p_price > 0:
            details += f"- Giá bán gốc: {p_price:,.0f} VNĐ\n"
        if p_promo is not None and p_promo > 0:
            details += f"- Giá khuyến mại trong live: {p_promo:,.0f} VNĐ\n"
        if p_desc:
            details += f"- Mô tả chi tiết: {p_desc}\n"
        if p_feat:
            details += f"- Tính năng nổi bật & Điểm mạnh (USP): {p_feat}\n"
        if p_ingr:
            details += f"- Thành phần / Chất liệu / Thông số: {p_ingr}\n"

        custom_section = ""
        if key_highlights:
            custom_section += f"- Điểm nhấn đặc biệt cần xoáy sâu: {key_highlights}\n"
        if promotions_deal:
            custom_section += f"- Chương trình ưu đãi / Deal sốc hôm nay: {promotions_deal}\n"
        if custom_instructions:
            custom_section += f"- Yêu cầu bổ sung của người dùng: {custom_instructions}\n"

        target_words = int(duration_minutes * 160)

        user_prompt = (
            f"Hãy viết kịch bản livestream bán hàng cho sản phẩm sau:\n\n"
            f"{details}\n"
            f"CẤU HÌNH LIVESTREAM:\n"
            f"- Phong cách nói: {style_note}\n"
            f"- Cách xưng hô với người xem: {honorific}\n"
            f"- Khách hàng mục tiêu: {target_audience}\n"
            f"- Tốc độ nói: {speed_note}\n"
            f"- Kêu gọi hành động (CTA): {cta_text or 'Bấm ngay vào giỏ hàng góc trái màn hình'}\n"
            f"- Thời lượng ước tính: khoảng {duration_minutes} phút (~ {target_words} từ).\n"
            f"{custom_section}\n"
            f"BẮT ĐẦU VIẾT LỜI THOẠI LIVESTREAM:"
        )

        return system_prompt, user_prompt

    async def generate_script_text(
        self,
        system_prompt: str,
        user_prompt: str,
        provider: Optional[BaseLLMProvider] = None,
    ) -> str:
        """Call LLM provider to generate script text and return clean spoken pitch."""
        if not provider:
            raise ValueError("Chưa cấu hình AI Provider hợp lệ.")
        raw = await provider.generate_text(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.75,
            max_tokens=1500,
        )
        try:
            import json, re
            clean_raw = raw.strip()
            if clean_raw.startswith("```"):
                lines = clean_raw.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_raw = "\n".join(lines).strip()

            data = json.loads(clean_raw)
            if isinstance(data, dict):
                segs = data.get("segments", [])
                texts = [s.get("text", "").strip() for s in segs if isinstance(s, dict) and s.get("text")]
                if texts:
                    return " ".join(texts)
                if "text" in data and isinstance(data["text"], str):
                    return data["text"].strip()
        except Exception:
            pass

        try:
            import re
            matches = re.findall(r'"text":\s*"([^"]+)"', raw)
            if matches:
                return " ".join(matches)
        except Exception:
            pass

        return raw.strip()

    async def generate_script_with_ai(
        self,
        product: Optional[Product] = None,
        provider: Optional[BaseLLMProvider] = None,
        product_name: str = "",
        price: Optional[float] = None,
        promo_price: Optional[float] = None,
        description: str = "",
        verified_features: str = "",
        ingredients: str = "",
        script_type: str = "full_live",
        style: str = "energetic",
        target_audience: str = "Tất cả khách hàng",
        speaking_speed: str = "normal",
        honorific: str = "Anh/Chị",
        promotions_deal: Optional[str] = None,
        key_highlights: Optional[str] = None,
        cta_text: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        duration_minutes: float = 2.0,
    ) -> Script:
        """Generate a livestream script using AI provider and save to DB."""
        if not provider:
            raise ValueError("Chưa cấu hình AI Provider hợp lệ.")

        system_prompt, user_prompt = self.build_script_prompt(
            product=product,
            product_name=product_name,
            price=price,
            promo_price=promo_price,
            description=description,
            verified_features=verified_features,
            ingredients=ingredients,
            script_type=script_type,
            style=style,
            target_audience=target_audience,
            speaking_speed=speaking_speed,
            honorific=honorific,
            promotions_deal=promotions_deal,
            key_highlights=key_highlights,
            cta_text=cta_text,
            custom_instructions=custom_instructions,
            duration_minutes=duration_minutes,
        )

        content = await provider.generate_text(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.75,
            max_tokens=2500,
        )

        if not content or len(content.strip()) < 20:
            raise RuntimeError("Nội dung AI trả về quá ngắn hoặc rỗng.")

        from app.services.script_segment_service import parse_and_validate_script_json
        structured = parse_and_validate_script_json(content)
        saved_content = structured.to_json()
        word_count = len(structured.total_text.split())
        estimated_seconds = (word_count / 150) * 60

        resolved_name = product.name if product else (product_name or "Sản phẩm")
        type_titles = {
            "full_live": "Kịch bản Toàn Diện",
            "flash_sale": "Kịch bản Flash Sale",
            "storytelling": "Kịch bản Review Kể Chuyện",
            "objection_handling": "Kịch bản Xử Lý Từ Chối",
            "giveaway_hook": "Kịch bản Minigame Quà Tặng",
        }
        type_title = type_titles.get(script_type, "Kịch bản Livestream")
        title = f"{type_title} - {resolved_name}"

        product_id = product.id if product else None
        if not product_id:
            # If no product id, find or create ad-hoc product
            from app.database.repositories.product_repo import ProductRepository
            p_repo = ProductRepository(self.script_repo.session)
            new_p = p_repo.create({
                "name": resolved_name,
                "price": price or 0,
                "promo_price": promo_price or 0,
                "description": description or f"Sản phẩm {resolved_name} tạo từ AI Script Studio",
                "category": "Kịch bản tùy chỉnh",
            })
            product_id = new_p.id

        script = self.script_repo.create(
            product_id=product_id,
            title=title,
            content=saved_content,
            style=style,
            target_audience=target_audience,
            language="vi",
            speaking_speed=speaking_speed,
            honorific=honorific,
            cta_text=cta_text or promotions_deal,
            duration_seconds=estimated_seconds,
        )
        return script

    async def suggest_script_config(
        self,
        product_info: Dict[str, Any],
        provider: BaseLLMProvider,
    ) -> Dict[str, str]:
        """Ask AI to analyze the product and suggest optimal script configuration."""
        name = product_info.get("name", "Sản phẩm")
        price = product_info.get("price", 0)
        desc = product_info.get("description", "")
        features = product_info.get("verified_features", "")

        prompt = (
            f"Phân tích sản phẩm sau để gợi ý cấu hình livestream bán hàng tối ưu:\n"
            f"- Tên sản phẩm: {name}\n"
            f"- Giá bán: {price:,.0f} đ\n"
            f"- Mô tả: {desc}\n"
            f"- Tính năng: {features}\n\n"
            "Hãy trả về MỘT ĐỐI TƯỢNG JSON DUY NHẤT (không dùng markdown codeblock, không ghi gì thêm) gồm 6 trường sau:\n"
            "{\n"
            '  "target_audience": "Mô tả ngắn đối tượng khách hàng mục tiêu phù hợp nhất",\n'
            '  "recommended_style": "energetic|consultant|friendly|humorous|luxury",\n'
            '  "recommended_type": "full_live|flash_sale|storytelling|objection_handling",\n'
            '  "key_highlights": "2-3 điểm bán hàng đắt giá (USP) cần làm nổi bật",\n'
            '  "promotions_deal": "Gợi ý chương trình ưu đãi / quà tặng / voucher kích thích chốt đơn",\n'
            '  "cta_text": "Câu kêu gọi hành động (CTA) ngắn gọn, thúc giục bấm giỏ hàng"\n'
            "}"
        )

        raw = await provider.generate_text(prompt, temperature=0.5)
        # Extract JSON
        clean_raw = raw.strip()
        if "```" in clean_raw:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_raw, re.DOTALL)
            if match:
                clean_raw = match.group(1)
            else:
                clean_raw = clean_raw.replace("```json", "").replace("```", "").strip()

        try:
            data = json.loads(clean_raw)
            return {
                "target_audience": str(data.get("target_audience", "Khách hàng quan tâm sản phẩm")),
                "recommended_style": str(data.get("recommended_style", "energetic")),
                "recommended_type": str(data.get("recommended_type", "full_live")),
                "key_highlights": str(data.get("key_highlights", "")),
                "promotions_deal": str(data.get("promotions_deal", "")),
                "cta_text": str(data.get("cta_text", "Nhấp ngay vào giỏ hàng bên dưới để nhận ưu đãi!")),
            }
        except Exception as e:
            logger.warning(f"Không thể parse JSON gợi ý cấu hình: {e}. Raw: {raw[:200]}")
            return {
                "target_audience": "Tất cả khách hàng đang xem live",
                "recommended_style": "energetic",
                "recommended_type": "full_live",
                "key_highlights": features or desc[:100],
                "promotions_deal": "Ưu đãi độc quyền giảm giá trực tiếp trong livestream hôm nay!",
                "cta_text": "Bấm vào góc trái màn hình thêm ngay vào giỏ hàng!",
            }

    async def improve_script_with_ai(
        self,
        current_content: str,
        action: str,
        provider: BaseLLMProvider,
        custom_note: Optional[str] = None,
    ) -> str:
        """Improve, polish, or rewrite an existing script."""
        action_instructions = {
            "energetic": (
                "Hãy viết lại bài nói livestream sau với năng lượng BÙNG NỔ hơn, "
                "sử dụng câu từ hào hứng, nhiệt huyết, đẩy mạnh cảm xúc phấn khích và kích thích người xem mua hàng ngay."
            ),
            "shorten": (
                "Hãy rút ngắn bài nói sau cô đọng, súc tích hơn (giảm khoảng 30-40% độ dài), "
                "lược bỏ các câu dài dòng, giữ lại trọn vẹn các ý đắt giá và câu chốt đơn mạnh nhất."
            ),
            "fomo": (
                "Hãy bổ sung mạnh mẽ yếu tố KHAN HIẾM & ĐẾM NGƯỢC (FOMO): "
                "Nhấn mạnh chỉ còn số lượng cực ít (5-10 suất cuối), thời gian voucher sắp hết hạn, hối thúc chốt đơn ngay kẻo tiếc nuối."
            ),
            "interactive": (
                "Hãy bổ sung thêm các câu hỏi tương tác tự nhiên với người xem: "
                "kêu gọi thả tim đạt mốc để tung quà, hỏi số cân nặng/size/màu sắc của người xem để streamer tư vấn trực tiếp."
            ),
            "custom": f"Hãy chỉnh sửa kịch bản theo yêu cầu sau của người dùng: {custom_note or 'Tối ưu cho livestream bán hàng'}",
        }.get(action, "Hãy tối ưu lại kịch bản livestream bán hàng sau cho hấp dẫn hơn.")

        prompt = (
            f"{action_instructions}\n\n"
            f"KỊCH BẢN HIỆN TẠI:\n"
            f"\"\"\"\n{current_content}\n\"\"\"\n\n"
            f"YÊU CẦU: Trả về toàn bộ nội dung kịch bản mới đã được cải thiện, chỉ trả về lời thoại trực tiếp."
        )

        new_content = await provider.generate_text(prompt, temperature=0.7)
        return new_content.strip()

    async def generate_product_details_with_ai(
        self,
        product_name: str,
        category: str = "",
        price: Optional[float] = None,
        provider: Optional[BaseLLMProvider] = None,
    ) -> Dict[str, str]:
        """Automatically generate rich product details (description, features, audience, etc.) for product creation."""
        if not provider:
            raise ValueError("Chưa cấu hình AI Provider.")

        prompt = (
            f"Bạn là chuyên gia thương mại điện tử và sản phẩm. Dựa vào tên sản phẩm '{product_name}' "
            f"(Danh mục: {category or 'Tự động xác định'}, Giá tham khảo: {price or 0:,.0f} đ), "
            "hãy soạn BẢN NHÁP thông tin chi tiết để người bán kiểm tra và hoàn thiện trước khi livestream. "
            "Chỉ ghi các đặc điểm phổ biến, mang tính mô tả của loại sản phẩm này; KHÔNG bịa chứng nhận, số liệu, "
            "công dụng y tế/làm đẹp vượt trội hay cam kết hiệu quả. Với thông tin cần người bán xác minh, thêm hậu tố (cần xác minh).\n\n"
            "Trả về MỘT ĐỐI TƯỢNG JSON DUY NHẤT (không dùng markdown codeblock, không ghi giải thích) với các trường:\n"
            "{\n"
            '  "category": "Tên danh mục phù hợp (VD: Thời trang, Mỹ phẩm, Đồ gia dụng...)",\n'
            '  "description": "Mô tả sản phẩm chi tiết, hấp dẫn, nêu rõ công dụng và điểm nổi bật (khoảng 3-4 câu)",\n'
            '  "verified_features": "3-5 tính năng nổi bật vượt trội (USP), mỗi tính năng trên 1 dòng",\n'
            '  "ingredients": "Chất liệu hoặc thành phần chính của sản phẩm",\n'
            '  "target_audience": "Đối tượng khách hàng sử dụng phù hợp nhất",\n'
            '  "usage_instructions": "Hướng dẫn sử dụng cơ bản và bảo quản",\n'
            '  "warnings": "Lưu ý hoặc cảnh báo an toàn khi sử dụng"\n'
            "}"
        )

        raw = await provider.generate_text(prompt, temperature=0.6)
        clean_raw = raw.strip()
        if "```" in clean_raw:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_raw, re.DOTALL)
            if match:
                clean_raw = match.group(1)
            else:
                clean_raw = clean_raw.replace("```json", "").replace("```", "").strip()

        try:
            data = json.loads(clean_raw)
            return {
                "category": str(data.get("category", category)),
                "description": str(data.get("description", "")),
                "verified_features": str(data.get("verified_features", "")),
                "ingredients": str(data.get("ingredients", "")),
                "target_audience": str(data.get("target_audience", "")),
                "usage_instructions": str(data.get("usage_instructions", "")),
                "warnings": str(data.get("warnings", "")),
            }
        except Exception as e:
            logger.warning(f"Không thể parse JSON chi tiết sản phẩm: {e}")
            return {
                "category": category or "Sản phẩm tổng hợp",
                "description": f"Sản phẩm {product_name} chất lượng cao, thiết kế hiện đại, đáp ứng tối đa nhu cầu của khách hàng.",
                "verified_features": "- Thiết kế tiện lợi\n- Chất liệu cao cấp bền bỉ\n- Hiệu năng vượt trội trong tầm giá",
                "ingredients": "Chất liệu cao cấp",
                "target_audience": "Mọi đối tượng khách hàng",
                "usage_instructions": "Sử dụng theo hướng dẫn đi kèm sản phẩm.",
                "warnings": "Bảo quản nơi khô ráo, thoáng mát.",
            }

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
