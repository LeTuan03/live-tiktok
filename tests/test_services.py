"""Services Unit Tests using standard unittest."""

import unittest

from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.repositories.comment_repo import CommentRepository
from app.database.session import init_database, get_session
from app.services.script_service import ScriptService
from app.services.voice_service import VoiceService
from app.services.comment_service import CommentService
from app.services.product_service import ProductService


class TestServices(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_database("sqlite:///:memory:")

    def setUp(self):
        self.session = get_session()

    def tearDown(self):
        self.session.close()

    def test_script_prompt_builder(self):
        prod_repo = ProductRepository()
        script_repo = ScriptRepository(self.session)
        product = prod_repo.create({"name": "Son Thỏi Matte", "price": 250000, "description": "Son lì không trôi"})

        service = ScriptService(script_repo)
        sys_prompt, user_prompt = service.build_script_prompt(
            product=product,
            style="consultant",
            honorific="Chị em",
            duration_minutes=1.5,
        )

        self.assertIn("Son Thỏi Matte", user_prompt)
        self.assertIn("Chị em", user_prompt)
        self.assertIn("250,000 VNĐ", user_prompt)

    def test_script_prompt_ad_hoc_and_custom_types(self):
        script_repo = ScriptRepository(self.session)
        service = ScriptService(script_repo)

        sys_prompt, user_prompt = service.build_script_prompt(
            product_name="Áo Thun Thể Thao Nam",
            price=199000,
            promo_price=149000,
            script_type="flash_sale",
            style="energetic",
            honorific="Các bác",
            promotions_deal="Mua 2 tặng 1 chỉ trong 10 phút",
            key_highlights="Co giãn 4 chiều, chống nhăn",
            custom_instructions="Nói giọng sôi nổi bắt trend",
            duration_minutes=1.0,
        )

        self.assertIn("Áo Thun Thể Thao Nam", user_prompt)
        self.assertIn("199,000 VNĐ", user_prompt)
        self.assertIn("149,000 VNĐ", user_prompt)
        self.assertIn("Mua 2 tặng 1", user_prompt)
        self.assertIn("Co giãn 4 chiều", user_prompt)
        self.assertIn("Các bác", user_prompt)
        self.assertIn("Flash Sale Chớp Nhoáng", sys_prompt)

    def test_script_generation_ad_hoc(self):
        import asyncio
        from unittest.mock import AsyncMock

        script_repo = ScriptRepository(self.session)
        service = ScriptService(script_repo)

        mock_provider = AsyncMock()
        mock_provider.generate_text.return_value = "Xin chào các tình yêu! Hôm nay em mang đến deal sốc chưa từng có..."

        loop = asyncio.new_event_loop()
        script = loop.run_until_complete(
            service.generate_script_with_ai(
                provider=mock_provider,
                product_name="Kem Dưỡng Da Ban Đêm",
                price=320000,
                promo_price=249000,
                script_type="full_live",
                style="friendly",
                honorific="Chị em",
            )
        )
        loop.close()

        self.assertIsNotNone(script)
        self.assertIn("Kem Dưỡng Da Ban Đêm", script.title)
        self.assertEqual(script.style, "friendly")
        self.assertIn("deal sốc", script.content)

    def test_voice_hash_computation(self):
        voice_repo = VoiceRepository(self.session)
        service = VoiceService(voice_repo)

        hash1 = service.compute_audio_hash("Xin chào", "vi-VN-HoaiMyNeural", 1.0)
        hash2 = service.compute_audio_hash("Xin chào", "vi-VN-HoaiMyNeural", 1.0)
        hash3 = service.compute_audio_hash("Xin chào", "vi-VN-NamMinhNeural", 1.0)

        self.assertEqual(hash1, hash2)
        self.assertNotEqual(hash1, hash3)

    def test_comment_matching_service(self):
        comment_repo = CommentRepository(self.session)
        service = CommentService(comment_repo)

        event, sug = service.process_comment("UserA", "Shop cho mình xin báo giá sản phẩm với ạ?")
        self.assertTrue("giá" in sug.suggested_reply.lower() or "giỏ hàng" in sug.suggested_reply.lower())

    def test_product_service_list_products(self):
        prod_repo = ProductRepository(self.session)
        prod_repo.create({"name": "Kem Chống Nắng SPF50", "price": 180000})

        service = ProductService(prod_repo)
        products = service.list_products()
        self.assertGreaterEqual(len(products), 1)
        self.assertTrue(any(p.name == "Kem Chống Nắng SPF50" for p in products))

    def test_product_extractor_service(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.services.product_extractor_service import ProductExtractorService

        extractor = ProductExtractorService()
        mock_provider = AsyncMock()
        mock_provider.generate_text.return_value = '''{
            "name": "Tai Nghe Bluetooth Không Dây True Wireless",
            "category": "Thiết bị điện tử",
            "price": 450000,
            "promo_price": 299000,
            "description": "Tai nghe bluetooth chất âm sống động, pin trâu 30h.",
            "verified_features": "- Chống ồn chủ động ANC\\n- Pin 30 giờ\\n- Chống nước IPX5",
            "target_audience": "Người yêu âm nhạc, game thủ"
        }'''

        loop = asyncio.new_event_loop()
        data = loop.run_until_complete(
            extractor.extract_product_from_url("https://shopee.vn/tai-nghe-bluetooth-i.123.456", mock_provider)
        )
        loop.close()

        self.assertEqual(data["name"], "Tai Nghe Bluetooth Không Dây True Wireless")
        self.assertEqual(data["price"], 450000.0)
        self.assertEqual(data["promo_price"], 299000.0)
        self.assertIn("Chống ồn chủ động", data["verified_features"])


if __name__ == "__main__":
    unittest.main()
