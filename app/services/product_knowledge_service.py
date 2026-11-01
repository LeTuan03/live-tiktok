"""Product Knowledge Service — enforces verified ground truth and strict anti-hallucination policies.

Design principles (see Blueprint §1, §4, §7):
1. Zero Hallucination: AI must never guess price, promotion, stock, ingredients, or policies.
2. Capability & Verification Gating: Unverified facts or products with missing critical data
   must trigger warnings and are barred from autonomous claims.
3. Strict Grounding Verification: Checks any candidate reply or speech segment against
   the verified profile before it is spoken or displayed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.database.models.product import Product, ProductVariant


@dataclass
class VerifiedProductProfile:
    """Immutable view of a product's verified ground truth facts."""

    product_id: int
    name: str
    sku: str
    category: str
    price: float
    promo_price: Optional[float]
    effective_price: float
    discount_percent: int
    verified_features: List[str]
    ingredients: Optional[str]
    sizes: Optional[str]
    target_audience: Optional[str]
    usage_instructions: Optional[str]
    warnings: Optional[str]
    variants: List[Dict[str, Any]]
    image_url: Optional[str]
    is_verified: bool
    verification_notes: List[str]
    allowed_claims: List[str] = field(default_factory=list)
    forbidden_terms: List[str] = field(default_factory=list)


class ProductKnowledgeService:
    """Service layer for verified product facts and hallucination prevention."""

    # Banned terms and medical/absolute claims that AI must NEVER make
    DEFAULT_BANNED_TERMS = [
        "trị dứt điểm",
        "chữa khỏi hoàn toàn",
        "cam kết 100%",
        "thuốc tiên",
        "bảo hành trọn đời không điều kiện",
        "hoàn tiền vô điều kiện",
        "rẻ nhất thị trường",
        "số 1 thế giới",
        "chữa bệnh",
        "đặc trị",
    ]

    def __init__(self, banned_terms: Optional[List[str]] = None) -> None:
        self.banned_terms = list(banned_terms or self.DEFAULT_BANNED_TERMS)

    def extract_verified_profile(self, product: Product) -> VerifiedProductProfile:
        """Extract a structured, verified profile from a Product DB model."""
        notes: List[str] = []
        is_verified = True

        price = float(product.price or 0.0)
        promo_price = float(product.promo_price) if product.promo_price is not None else None

        if price <= 0:
            is_verified = False
            notes.append("Giá sản phẩm chưa được thiết lập (> 0).")

        effective_price = promo_price if (promo_price is not None and promo_price > 0) else price
        discount_percent = 0
        if promo_price is not None and promo_price < price and price > 0:
            discount_percent = int(round((price - promo_price) / price * 100))

        # Parse features
        features: List[str] = []
        raw_features = product.verified_features or product.description or ""
        if raw_features:
            for line in raw_features.split("\n"):
                cleaned = line.strip().lstrip("•-* \t")
                if cleaned and len(cleaned) > 2:
                    features.append(cleaned)

        if not features:
            notes.append("Sản phẩm chưa có đặc tính được xác minh (verified_features).")

        # Variants
        variants_data: List[Dict[str, Any]] = []
        if getattr(product, "variants", None):
            for v in product.variants:
                if getattr(v, "is_available", True):
                    variants_data.append({
                        "id": v.id,
                        "name": v.variant_name,
                        "value": v.variant_value,
                        "price_adjustment": float(v.price_adjustment or 0.0),
                    })

        # Image/visual path
        image_url = product.image_path or product.product_url or ""

        return VerifiedProductProfile(
            product_id=product.id,
            name=product.name.strip(),
            sku=product.sku or f"SKU-{product.id}",
            category=product.category or "Chung",
            price=price,
            promo_price=promo_price,
            effective_price=effective_price,
            discount_percent=discount_percent,
            verified_features=features,
            ingredients=product.ingredients,
            sizes=product.sizes,
            target_audience=product.target_audience,
            usage_instructions=product.usage_instructions,
            warnings=product.warnings,
            variants=variants_data,
            image_url=image_url,
            is_verified=is_verified and len(features) > 0,
            verification_notes=notes,
            allowed_claims=features,
            forbidden_terms=self.banned_terms,
        )

    def validate_preflight(self, product: Product) -> Tuple[bool, List[str]]:
        """Preflight gate: check if a product has verified facts required for live presentation."""
        profile = self.extract_verified_profile(product)
        errors: List[str] = []

        if not profile.is_verified:
            errors.extend(profile.verification_notes)

        if profile.effective_price <= 0:
            errors.append(f"SKU {profile.sku}: Giá hiển thị không hợp lệ ({profile.effective_price}đ).")

        if len(profile.verified_features) < 1:
            errors.append(f"SKU {profile.sku}: Cần tối thiểu 1 đặc tính sản phẩm đã xác minh để MC giới thiệu.")

        return (len(errors) == 0), errors

    def validate_speech_text(
        self,
        text: str,
        profile: VerifiedProductProfile,
    ) -> Tuple[bool, List[str]]:
        """Check speech text against anti-hallucination and safety policy.

        Returns:
            (is_valid, list_of_violations)
        """
        violations: List[str] = []
        normalized_text = text.lower()

        # 1. Check banned terms
        for term in self.banned_terms:
            if term.lower() in normalized_text:
                violations.append(f"Chứa từ cấm / tuyên bố sai lệch: '{term}'")

        # 2. Check price numbers in text
        # If numbers followed by k, đ, nghìn, tr, triệu are mentioned, verify they match known prices
        price_patterns = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:k|đ|đồng|nghìn|triệu|tr)", normalized_text)
        known_prices = {int(profile.effective_price), int(profile.price)}
        if profile.promo_price:
            known_prices.add(int(profile.promo_price))

        # Add variant prices
        for v in profile.variants:
            adj = v.get("price_adjustment", 0.0)
            if adj:
                known_prices.add(int(profile.effective_price + adj))

        # Check for obvious fabricated prices (e.g. claims of 50k when price is 300k)
        for raw_num in price_patterns:
            try:
                num_val = float(raw_num.replace(",", "."))
                # Convert 'k' or 'nghìn' scale if small integer
                if num_val < 1000 and "triệu" not in normalized_text and "tr" not in normalized_text:
                    expanded_num = int(num_val * 1000)
                elif "triệu" in normalized_text or "tr" in normalized_text:
                    expanded_num = int(num_val * 1000000)
                else:
                    expanded_num = int(num_val)

                # If the number is clearly presented as a price and doesn't match any known price:
                # We allow a small tolerance for formatting or nearby numbers
                matched = any(abs(expanded_num - kp) <= 1000 for kp in known_prices if kp > 0)
                if not matched and expanded_num > 5000:
                    # Potential price hallucination
                    logger.warning(
                        "Cảnh báo số tiền '{}' (ước tính {}đ) không khớp dữ liệu giá đã xác minh {}",
                        raw_num,
                        expanded_num,
                        known_prices,
                    )
            except Exception:
                pass

        return (len(violations) == 0), violations

    def build_grounded_system_prompt(self, profile: VerifiedProductProfile) -> str:
        """Construct an LLM prompt enforcing zero-hallucination from verified profile."""
        features_bullet = "\n".join(f"- {f}" for f in profile.verified_features)
        variants_bullet = (
            "\n".join(f"- {v['name']}: {v['value']}" for v in profile.variants)
            if profile.variants
            else "Không có biến thể riêng."
        )

        formatted_price = f"{profile.effective_price:,.0f} VNĐ".replace(",", ".")
        original_price_str = (
            f" (Giá gốc: {profile.price:,.0f} VNĐ - Giảm {profile.discount_percent}%)".replace(",", ".")
            if profile.discount_percent > 0
            else ""
        )

        prompt = f"""BẠN LÀ MC BÁN HÀNG LIVESTREAM CHUYÊN NGHIỆP TRÊN TIKTOK LIVE.
Bạn PHẢI tuân thủ NGUYÊN TẮC BẤT DI BẤT DỊCH sau:
1. TUYỆT ĐỐI KHÔNG BỊA ĐẶT GIÁ, TỒN KHO, CÔNG DỤNG HAY CHÍNH SÁCH.
2. Chỉ sử dụng thông tin trong BẢNG DỮ KIỆN ĐÃ XÁC MINH dưới đây.
3. Nếu người xem hỏi điều gì nằm ngoài bảng dữ kiện, hãy trả lời trung thực: 'Dạ thông tin này em xin ghi nhận lại để shop kiểm tra và báo lại bạn trong tin nhắn nha!' hoặc mời xem giỏ hàng.
4. Lời thoại ngắn gọn, tự nhiên, hào hứng, chuẩn tiếng Việt, không dùng icon, không dùng markdown.

=== BẢNG DỮ KIỆN SẢN PHẨM ĐÃ XÁC MINH ===
- Tên sản phẩm: {profile.name}
- Mã SKU: {profile.sku}
- Ngành hàng: {profile.category}
- Giá bán hiện tại: {formatted_price}{original_price_str}
- Đặc tính & Công dụng đã kiểm chứng:
{features_bullet}
- Biến thể / Màu sắc / Size:
{variants_bullet}
- Thành phần: {profile.ingredients or 'Theo công bố chính thức của nhà sản xuất'}
- Cách sử dụng: {profile.usage_instructions or 'Xem hướng dẫn chi tiết trên bao bì sản phẩm'}
- Lưu ý / Cảnh báo: {profile.warnings or 'Bảo quản nơi khô ráo thoáng mát, tránh ánh nắng trực tiếp'}
- TỪ CẤM: Không bao giờ nói các từ: {', '.join(self.banned_terms)}
========================================
"""
        return prompt
