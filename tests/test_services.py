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


if __name__ == "__main__":
    unittest.main()
