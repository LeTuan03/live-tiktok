"""Unit and integration tests for Phase 5 — Screen OCR & Grounded Auto-Replies (§6 P5)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.comments.reply_clips import ReplyClipManager
from app.comments.sources.screen_ocr import ExtractedComment, ScreenOCRRegion, ScreenOCRService
from app.database.models.product import Product
from app.playout.engine import PlayoutEngine, PlayoutItem
from app.services.comment_reply_service import GroundedCommentReplyService
from app.services.comment_service import CommentService


class TestScreenOCRService:
    """Acceptance criteria P5: OCR parsing, Vietnamese comment extraction, and 0 duplicate processing."""

    def test_screen_ocr_region_bbox(self) -> None:
        region = ScreenOCRRegion(x=100, y=200, width=400, height=600)
        assert region.bbox == (100, 200, 500, 800)

    def test_parse_comments_with_various_formats(self) -> None:
        service = ScreenOCRService()
        raw_text = """
        UserA: Sản phẩm này giá bao nhiêu vậy shop?
        [UserB] Có freeship không ạ?
        vừa tham gia livestream
        UserC : Shop ơi còn màu xanh không?
        đã theo dõi shop
        UserD: 100k được không
        """
        comments = service.parse_comments(raw_text)

        # 4 valid comments, 2 system lines filtered out
        assert len(comments) == 4
        usernames = [c.username for c in comments]
        assert usernames == ["UserA", "UserB", "UserC", "UserD"]
        assert "giá bao nhiêu" in comments[0].text
        assert "freeship" in comments[1].text
        assert "màu xanh" in comments[2].text

    def test_zero_duplicates_processed_guarantee(self) -> None:
        """P5 criteria: 0 bình luận bị xử lý trùng."""
        service = ScreenOCRService()
        raw_text = "ThanhHang: Áo này có size L không shop?"

        # First poll
        comments_1 = service.parse_comments(raw_text)
        filtered_1 = service.filter_duplicates(comments_1)
        assert len(filtered_1) == 1
        assert filtered_1[0].username == "ThanhHang"

        # Second poll with identical text in frame
        comments_2 = service.parse_comments(raw_text)
        filtered_2 = service.filter_duplicates(comments_2)
        assert len(filtered_2) == 0, "Duplicate comment MUST be filtered out (0 duplicate processing)"

        # Third poll with new comment
        raw_text_new = """
        ThanhHang: Áo này có size L không shop?
        MinhTuan: Em đặt 2 chiếc rồi nha
        """
        comments_3 = service.parse_comments(raw_text_new)
        filtered_3 = service.filter_duplicates(comments_3)
        assert len(filtered_3) == 1
        assert filtered_3[0].username == "MinhTuan"


class TestGroundedCommentReplyService:
    """Acceptance criteria P5: 0 câu trả lời chứa thông tin không có trong dữ liệu sản phẩm."""

    @pytest.fixture
    def comment_service(self) -> CommentService:
        repo_mock = MagicMock()
        return CommentService(comment_repo=repo_mock)

    @pytest.fixture
    def valid_product(self) -> Product:
        prod = MagicMock(spec=Product)
        prod.id = 1
        prod.name = "Nồi Chiên Không Dầu Sunhouse"
        prod.price = 1500000.0
        prod.promo_price = 999000.0
        prod.sizes = "Dung tích 6L, 9L"
        prod.verified_features = "Công nghệ Rapid Air giảm 80% dầu mỡ; Lòng nồi chống dính Teflon"
        prod.ingredients = "Hợp kim nhôm, nhựa ABS cao cấp"
        prod.usage_instructions = "Cắm điện, chọn nhiệt độ 180 độ C trong 15 phút"
        return prod

    def test_grounded_reply_for_price_with_verified_data(
        self, comment_service: CommentService, valid_product: Product
    ) -> None:
        service = GroundedCommentReplyService(comment_service=comment_service)
        comment = ExtractedComment(
            username="MaiAnh",
            text="Giá bao nhiêu vậy shop ơi?",
            raw_line="MaiAnh: Giá bao nhiêu vậy shop ơi?",
            comment_hash="h1",
        )

        can_reply, reply, reason = service.evaluate_comment_for_reply(comment, active_product=valid_product)
        assert can_reply is True
        assert reply is not None
        assert "999.000đ" in reply
        assert "1.500.000đ" in reply

    def test_strict_refusal_when_product_data_is_missing(
        self, comment_service: CommentService
    ) -> None:
        """P5 requirement: Never hallucinate missing facts."""
        empty_prod = MagicMock(spec=Product)
        empty_prod.id = 2
        empty_prod.name = "Sản phẩm thử nghiệm"
        empty_prod.price = None
        empty_prod.promo_price = None
        empty_prod.sizes = None
        empty_prod.verified_features = None

        service = GroundedCommentReplyService(comment_service=comment_service)
        comment = ExtractedComment(
            username="HungLe",
            text="Nồi này mấy tiền vậy?",
            raw_line="HungLe: Nồi này mấy tiền vậy?",
            comment_hash="h2",
        )

        can_reply, reply, reason = service.evaluate_comment_for_reply(comment, active_product=empty_prod)
        # MUST refuse autonomous reply to prevent hallucinating fake price
        assert can_reply is False
        assert reply is None
        assert "Thiếu dữ liệu xác minh" in reason

    def test_blocks_spam_and_inappropriate(
        self, comment_service: CommentService, valid_product: Product
    ) -> None:
        service = GroundedCommentReplyService(comment_service=comment_service)
        spam_comment = ExtractedComment(
            username="Spammer",
            text="Kết bạn Zalo 0912345678 để kiếm tiền online",
            raw_line="Spammer: Kết bạn Zalo 0912345678",
            comment_hash="h3",
        )
        can_reply, reply, reason = service.evaluate_comment_for_reply(spam_comment, active_product=valid_product)
        assert can_reply is False
        assert "bị chặn" in reason


class TestReplyClipManager:
    """Acceptance criteria P5: Generates card / reply clip and interjects into PlayoutEngine in priority queue."""

    def test_generate_reply_card_image(self, tmp_path: Path) -> None:
        manager = ReplyClipManager(storage_dir=tmp_path)
        out_png = tmp_path / "card.png"
        res = manager.generate_reply_card_image(
            output_png=out_png,
            customer_name="ThuTrang",
            reply_text="Dạ bạn ThuTrang ơi, sản phẩm này hiện đang giảm còn 199.000đ ạ!",
        )
        assert res.exists()
        assert res.stat().st_size > 1000

        with Image.open(res) as img:
            assert img.size == (1080, 1920)

    def test_enqueue_reply_inserts_at_head_of_playout_engine(self, tmp_path: Path) -> None:
        manager = ReplyClipManager(storage_dir=tmp_path)
        # Mock render to return quick file
        dummy_clip = tmp_path / "dummy_reply.mp4"
        dummy_clip.write_bytes(b"\x00" * 2000)
        manager.render_reply_clip = MagicMock(return_value=dummy_clip)

        normal_item1 = PlayoutItem(title="Item 1", video_path=str(dummy_clip))
        normal_item2 = PlayoutItem(title="Item 2", video_path=str(dummy_clip))
        engine = PlayoutEngine(playlist=[normal_item1, normal_item2], use_filler_fallback=False)

        ok = manager.enqueue_reply_to_engine(
            engine=engine,
            reply_text="Dạ sản phẩm bảo hành chính hãng 12 tháng ạ",
            customer_name="QuocBao",
        )

        assert ok is True
        # Must be in priority insertion queue
        assert len(engine.insertion_queue) == 1
        inserted = engine.insertion_queue[0]

        assert inserted.is_insertion is True
        assert "QuocBao" in inserted.title
