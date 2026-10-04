"""Product Extractor Service for scraping and AI parsing product details from URLs."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple
from urllib.parse import urlparse, unquote

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

from app.integrations.llm.base import BaseLLMProvider


class ProductExtractorService:
    """Service to automatically extract structured product information from e-commerce links."""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
    }

    async def fetch_url_content(self, url: str) -> Tuple[str, Dict[str, Any]]:
        """Fetch webpage HTML, extract metadata and visible text content.
        
        Returns:
            Tuple of (formatted_text_summary, metadata_dict)
        """
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        metadata: Dict[str, Any] = {
            "url": clean_url,
            "title": "",
            "og_image": "",
            "og_price": None,
        }

        html_text = ""
        fetch_error = ""

        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(headers=self.DEFAULT_HEADERS, timeout=timeout) as session:
                async with session.get(clean_url, allow_redirects=True) as response:
                    if response.status == 200:
                        html_text = await response.text(errors="ignore")
                    else:
                        fetch_error = f"HTTP {response.status}"
                        logger.warning(f"URL trả về mã {response.status}: {clean_url}")
        except Exception as e:
            fetch_error = str(e)
            logger.warning(f"Không thể tải trực tiếp URL {clean_url}: {e}")

        # Parse with BeautifulSoup if html received
        extracted_info = []
        extracted_info.append(f"Đường link sản phẩm: {clean_url}")

        if html_text:
            try:
                soup = BeautifulSoup(html_text, "html.parser")

                # Title
                title_tag = soup.find("title")
                title = title_tag.get_text().strip() if title_tag else ""
                metadata["title"] = title
                if title:
                    extracted_info.append(f"Tiêu đề trang: {title}")

                # Meta tags
                for meta in soup.find_all("meta"):
                    prop = meta.get("property", "").lower()
                    name = meta.get("name", "").lower()
                    content = meta.get("content", "").strip()
                    if not content:
                        continue

                    if prop == "og:title" or name == "twitter:title":
                        extracted_info.append(f"Tên sản phẩm (Meta): {content}")
                    elif prop == "og:description" or name in ("description", "twitter:description"):
                        extracted_info.append(f"Mô tả (Meta): {content}")
                    elif prop == "og:image" or name == "twitter:image":
                        metadata["og_image"] = content
                    elif "price" in prop or "price" in name:
                        extracted_info.append(f"Giá (Meta): {content}")

                # JSON-LD Schema
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        raw_json = script.string
                        if raw_json and "Product" in raw_json:
                            extracted_info.append(f"Dữ liệu có cấu trúc Schema Product:\n{raw_json[:1200]}")
                            break
                    except Exception:
                        pass

                # Remove non-content tags
                for tag in soup(["script", "style", "svg", "noscript", "header", "footer", "nav"]):
                    tag.decompose()

                body_text = soup.get_text(separator=" ", strip=True)
                clean_body = re.sub(r"\s+", " ", body_text)
                if clean_body:
                    extracted_info.append(f"Nội dung văn bản trên trang:\n{clean_body[:3500]}")

            except Exception as e:
                logger.warning(f"Lỗi phân tích HTML từ URL {clean_url}: {e}")

        # If html_text was empty or blocked, infer from URL structure / slug
        if len(extracted_info) <= 1:
            parsed = urlparse(clean_url)
            path_slug = unquote(parsed.path)
            extracted_info.append(
                f"(Trang web chặn quét trực tiếp do bảo mật [{fetch_error}], phân tích đường dẫn URL: {path_slug})"
            )

        summary_text = "\n\n".join(extracted_info)
        return summary_text, metadata

    async def extract_product_from_url(
        self,
        url: str,
        provider: BaseLLMProvider,
    ) -> Dict[str, Any]:
        """Scrape webpage and use AI to extract complete structured product parameters."""
        summary_text, meta = await self.fetch_url_content(url)

        prompt = (
            "Bạn là một Chuyên Gia Trích Xuất Dữ Liệu Sản Phẩm Thương Mại Điện Tử "
            "(TikTok Shop, Shopee, Lazada, Tiki, Website bán hàng).\n"
            f"Dưới đây là thông tin thu thập được từ liên kết: {url}\n\n"
            f"--- NỘI DUNG THU THẬP ĐƯỢC ---\n{summary_text}\n--- HẾT NỘI DUNG ---\n\n"
            "Hãy phân tích kỹ lưỡng và trích xuất thông tin sản phẩm thành MỘT ĐỐI TƯỢNG JSON DUY NHẤT "
            "(tuyệt đối không dùng markdown ```json, không viết bất kỳ lời dẫn nào):\n"
            "{\n"
            '  "name": "Tên sản phẩm chuẩn ngắn gọn, đúng tên thương mại",\n'
            '  "sku": "Mã SKU hoặc Model sản phẩm nếu có",\n'
            '  "category": "Danh mục (VD: Thời trang, Mỹ phẩm, Đồ gia dụng, Điện tử, Mẹ & Bé...)",\n'
            '  "price": 250000,\n'
            '  "promo_price": 199000,\n'
            '  "description": "Mô tả hấp dẫn, nêu bật công dụng và giá trị sản phẩm (2-4 câu)",\n'
            '  "verified_features": "- Tính năng 1\\n- Tính năng 2\\n- Tính năng 3 (mỗi tính năng trên 1 dòng)",\n'
            '  "ingredients": "Chất liệu, thành phần chính hoặc thông số kỹ thuật",\n'
            '  "sizes": "Kích thước, dung tích, biến thể hoặc màu sắc",\n'
            '  "target_audience": "Đối tượng khách hàng phù hợp nhất",\n'
            '  "usage_instructions": "Hướng dẫn sử dụng và bảo quản cơ bản",\n'
            '  "warnings": "Lưu ý hoặc cảnh báo an toàn khi dùng"\n'
            "}\n"
            "LƯU Ý QUAN TRỌNG:\n"
            "- price và promo_price phải là số nguyên (VNĐ), nếu không thấy giá khuyến mãi thì để promo_price bằng price hoặc null.\n"
            "- Chỉ trích xuất thông tin CÓ TRONG nội dung được cung cấp. Trường nào KHÔNG CÓ thông tin → trả null. "
            "KHÔNG ĐƯỢC suy đoán, ước lượng hay bịa giá, thành phần, chứng nhận, thông số kỹ thuật.\n"
            "- Nếu trang web bị chặn và không có nội dung, chỉ trả name từ URL và các trường còn lại để null."
        )

        raw = await provider.generate_text(prompt, temperature=0.3)
        clean_raw = raw.strip()

        # Clean JSON markdown fences
        if "```" in clean_raw:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_raw, re.DOTALL)
            if match:
                clean_raw = match.group(1)
            else:
                clean_raw = re.sub(r"```[a-z]*", "", clean_raw).replace("```", "").strip()

        try:
            data = json.loads(clean_raw)
        except Exception as e:
            logger.warning(f"Lỗi parse JSON trích xuất sản phẩm: {e}. Raw: {raw[:200]}")
            # Fallback parse title from metadata
            data = {
                "name": meta.get("title") or "Sản phẩm từ Link",
                "category": "Sản phẩm trực tuyến",
                "price": 0,
                "promo_price": 0,
                "description": f"Sản phẩm trích xuất từ link {url}",
                "verified_features": "- Trích xuất tự động từ đường link sản phẩm",
            }

        # Normalize fields
        price_val = data.get("price")
        promo_val = data.get("promo_price")

        try:
            price_float = float(price_val) if price_val is not None else 0.0
        except (ValueError, TypeError):
            price_float = 0.0

        try:
            promo_float = float(promo_val) if promo_val is not None else 0.0
        except (ValueError, TypeError):
            promo_float = 0.0

        return {
            "name": str(data.get("name") or meta.get("title") or "Sản phẩm mới"),
            "sku": str(data.get("sku") or ""),
            "category": str(data.get("category") or "Sản phẩm tổng hợp"),
            "price": price_float,
            "promo_price": promo_float,
            "description": str(data.get("description") or ""),
            "verified_features": str(data.get("verified_features") or ""),
            "ingredients": str(data.get("ingredients") or ""),
            "sizes": str(data.get("sizes") or ""),
            "target_audience": str(data.get("target_audience") or "Mọi khách hàng"),
            "usage_instructions": str(data.get("usage_instructions") or ""),
            "warnings": str(data.get("warnings") or ""),
            "product_url": url,
            "image_url": meta.get("og_image") or "",
        }
