"""Database & Repository Unit Tests using standard unittest."""

import unittest

from app.database.models.product import Product
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.repositories.comment_repo import CommentRepository
from app.database.session import init_database


class TestDatabaseRepositories(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_database("sqlite:///:memory:")

    def test_product_crud(self):
        repo = ProductRepository()
        product = repo.create({"name": "Áo thun Nam Premium", "price": 199000, "sku": "AT-001"})

        self.assertIsNotNone(product.id)
        self.assertEqual(product.name, "Áo thun Nam Premium")
        self.assertEqual(product.price, 199000)

        fetched = repo.get_by_id(product.id)
        self.assertEqual(fetched.sku, "AT-001")

        updated = repo.update(product.id, {"price": 179000})
        self.assertEqual(updated.price, 179000)

        self.assertTrue(repo.delete(product.id))
        self.assertIsNone(repo.get_by_id(product.id))

    def test_script_repository(self):
        prod_repo = ProductRepository()
        product = prod_repo.create({"name": "Giày Sneaker", "price": 499000})

        # Test script repo with a dummy session if needed
        from app.database.session import get_session
        session = get_session()
        script_repo = ScriptRepository(session)
        script = script_repo.create(
            product_id=product.id,
            title="Kịch bản Giày Sneaker",
            content="Chào mừng các bạn đến với buổi live mua sneaker...",
            style="energetic",
        )

        self.assertIsNotNone(script.id)
        self.assertEqual(script.version, 1)
        self.assertTrue(script.is_current)

        updated = script_repo.update_content(script.id, "Nội dung cập nhật v2", change_note="Chỉnh sửa mở đầu")
        self.assertEqual(updated.version, 2)

        versions = script_repo.get_versions(script.id)
        self.assertEqual(len(versions), 2)
        session.close()

    def test_voice_repository(self):
        from app.database.session import get_session
        session = get_session()
        voice_repo = VoiceRepository(session)
        profile = voice_repo.create_profile(
            name="Giọng Nam Chuẩn",
            provider="edge_tts",
            voice_id="vi-VN-NamMinhNeural",
            is_default=True,
        )
        self.assertIsNotNone(profile.id)
        self.assertTrue(profile.is_default)

        profiles = voice_repo.list_profiles()
        self.assertEqual(len(profiles), 1)
        session.close()

    def test_comment_repository(self):
        from app.database.session import get_session
        session = get_session()
        comment_repo = CommentRepository(session)
        event = comment_repo.create_comment_event(
            username="Viewer1",
            content="Giá bao nhiêu shop ơi?",
            category="price",
        )
        self.assertIsNotNone(event.id)

        sug = comment_repo.create_suggestion(
            comment_id=event.id,
            suggested_reply="Dạ giá 199k ạ!",
        )
        self.assertIsNotNone(sug.id)
        session.close()


if __name__ == "__main__":
    unittest.main()
