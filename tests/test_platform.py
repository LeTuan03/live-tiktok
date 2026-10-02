"""Capability gating, comment assistant grounding and no-fake-success guarantees."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import TikTokCapabilityUnsupportedError
from app.database.repositories.comment_repo import CommentRepository
from app.database.repositories.product_repo import ProductRepository
from app.integrations.tiktok.capabilities import Capability, CapabilityStatus, Platform, get_capabilities, is_supported
from app.services.comment_service import CommentService
from app.services.platform_service import PlatformService


# ------------------------------------------------------------ capabilities
@pytest.mark.parametrize("platform", [Platform.TIKTOK, Platform.SHOPEE])
def test_capability_matrix_is_complete_and_honest(platform):
    caps = get_capabilities(platform)
    assert set(caps) == set(Capability)
    # Streaming via OBS is the only supported capability; comments/pinning are manual.
    assert caps[Capability.STREAM_VIDEO].status == CapabilityStatus.SUPPORTED
    assert caps[Capability.SEND_COMMENT].status == CapabilityStatus.MANUAL
    assert caps[Capability.PIN_PRODUCT].status == CapabilityStatus.MANUAL
    assert caps[Capability.READ_COMMENTS].status != CapabilityStatus.SUPPORTED
    for info in caps.values():
        assert info.method and (info.status == CapabilityStatus.SUPPORTED or info.manual_steps)


def test_platform_service_never_fakes_success():
    svc = PlatformService(Platform.TIKTOK)
    with pytest.raises(TikTokCapabilityUnsupportedError):
        svc.send_comment("hello")
    with pytest.raises(TikTokCapabilityUnsupportedError):
        svc.pin_product(1)
    assert svc.get_pin_state(1) is None
    state = svc.mark_pinned_manually(1)
    assert state.confirmed_by == "user" and state.pinned
    assert not is_supported(Platform.SHOPEE, Capability.SEND_COMMENT)


# --------------------------------------------------------------- comments
def test_comment_classification():
    c = CommentService.classify
    assert c("Sản phẩm này bao nhiêu tiền vậy shop?") == "price"
    assert c("ship về Hà Nội mấy ngày?") == "shipping"
    assert c("còn size M không?") == "variant"
    assert c("bảo hành bao lâu") == "warranty"
    assert c("chất liệu gì vậy") == "feature"
    assert c("kết bạn zalo 0909") == "spam"
    assert c("hàng lừa đảo") == "inappropriate"
    assert c("xinh quá") == "other"


def test_grounded_reply_uses_only_product_data(session):
    product = ProductRepository().create({"name": "Camera Ezviz", "price": 900000, "promo_price": 735000, "sizes": "Trắng, Đen"})
    service = CommentService(CommentRepository(session))
    event, sug = service.process_comment("khach1", "giá bao nhiêu shop", product=product)
    assert event.category == "price" and sug is not None
    assert "735.000" in sug.suggested_reply and "900.000" in sug.suggested_reply
    assert sug.needs_data is False and sug.status == "pending"

    event, sug = service.process_comment("khach2", "ship bao lâu?", product=product, policies={})
    assert sug.needs_data is True  # no shipping policy configured → no promise
    assert "freeship" not in sug.suggested_reply.lower()

    event, sug = service.process_comment("khach3", "có màu gì?", product=product)
    assert "Trắng, Đen" in sug.suggested_reply

    event, sug = service.process_comment("spammer", "kết bạn zalo nhé", product=product)
    assert event.is_spam and sug is None


def test_reply_without_product_data_asks_to_check(session):
    service = CommentService(CommentRepository(session))
    _, sug = service.process_comment("u", "giá bao nhiêu", product=None)
    assert sug.needs_data and "kiểm tra" in sug.suggested_reply


def test_ai_reply_with_forbidden_claims_is_replaced(session):
    product = ProductRepository().create({"name": "Trà giảm cân", "price": 100000})
    service = CommentService(CommentRepository(session))
    provider = AsyncMock()
    provider.generate_text.return_value = '{"reply": "Uống 1 tuần giảm 5kg, cam kết hiệu quả 100%", "needs_data": false, "note": ""}'
    data = asyncio.run(service.suggest_with_ai("có hiệu quả không", "feature", product, provider))
    assert "100%" not in data["reply"] and "cam kết" not in data["reply"].lower()
    assert "an toàn" in data["note"].lower()


def test_ai_reply_valid_json_is_used(session):
    product = ProductRepository().create({"name": "Nồi", "price": 100000})
    service = CommentService(CommentRepository(session))
    provider = AsyncMock()
    provider.generate_text.return_value = 'Đây là JSON: {"reply": "Dạ nồi giá 100.000đ ạ", "needs_data": false, "note": "giá từ dữ liệu"}'
    data = asyncio.run(service.suggest_with_ai("giá?", "price", product, provider))
    assert data["reply"] == "Dạ nồi giá 100.000đ ạ" and data["needs_data"] is False


def test_send_reply_never_reports_sent_without_confirmation(session):
    service = CommentService(CommentRepository(session))
    _, sug = service.process_comment("u", "giá bao nhiêu", product=None)
    service.approve(sug.id, edited_reply="Dạ em kiểm tra lại ạ")
    with pytest.raises(TikTokCapabilityUnsupportedError):
        service.send_reply(sug.id)
    refreshed = service.comment_repo.get_suggestion(sug.id)
    assert refreshed.status == "approved"  # NOT "sent"
    assert refreshed.sent_at is None and refreshed.send_error
    # Operator confirms they pasted it themselves
    done = service.mark_sent_manually(sug.id)
    assert done.status == "sent_manual" and done.sent_at is not None


def test_auto_reply_cannot_be_enabled(session):
    service = CommentService(CommentRepository(session))
    assert service.auto_reply_enabled is False
    assert service.set_auto_reply(True) is False
    assert service.auto_reply_enabled is False
