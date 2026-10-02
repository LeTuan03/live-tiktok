"""Platform actions gated by the capability matrix (no fake success)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from loguru import logger

from app.core.exceptions import TikTokCapabilityUnsupportedError
from app.integrations.tiktok.capabilities import (
    Capability,
    CapabilityStatus,
    Platform,
    get_capability,
)


@dataclass
class PinState:
    product_id: int
    pinned: bool
    confirmed_by: str  # "platform" (API confirmation) or "user" (manual mark)
    at: datetime


class PlatformService:
    """Capability-gated operations for TikTok / Shopee.

    All automated paths raise :class:`TikTokCapabilityUnsupportedError` unless the
    matrix says the capability is SUPPORTED and the platform confirmed success.
    The manual paths only record what the operator tells us they did.
    """

    def __init__(self, platform: Platform | str = Platform.TIKTOK) -> None:
        self.platform = Platform(platform)
        self._pins: Dict[int, PinState] = {}

    # ------------------------------------------------------------ comments
    def send_comment(self, text: str) -> bool:
        info = get_capability(self.platform, Capability.SEND_COMMENT)
        if info.status != CapabilityStatus.SUPPORTED:
            raise TikTokCapabilityUnsupportedError(
                f"{Capability.SEND_COMMENT.display_name} ({self.platform.display_name}) — {info.status.label}"
            )
        # No supported platform exists yet; this branch is unreachable by design.
        raise TikTokCapabilityUnsupportedError(Capability.SEND_COMMENT.display_name)  # pragma: no cover

    # ------------------------------------------------------------- pinning
    def pin_product(self, product_id: int) -> bool:
        info = get_capability(self.platform, Capability.PIN_PRODUCT)
        if info.status != CapabilityStatus.SUPPORTED:
            raise TikTokCapabilityUnsupportedError(
                f"{Capability.PIN_PRODUCT.display_name} ({self.platform.display_name}) — {info.status.label}"
            )
        raise TikTokCapabilityUnsupportedError(Capability.PIN_PRODUCT.display_name)  # pragma: no cover

    def mark_pinned_manually(self, product_id: int, pinned: bool = True) -> PinState:
        state = PinState(
            product_id=product_id,
            pinned=pinned,
            confirmed_by="user",
            at=datetime.now(timezone.utc),
        )
        self._pins[product_id] = state
        logger.info("Người dùng đánh dấu sản phẩm {} {}", product_id, "đã ghim" if pinned else "bỏ ghim")
        return state

    def get_pin_state(self, product_id: int) -> Optional[PinState]:
        return self._pins.get(product_id)

    def manual_steps(self, capability: Capability) -> list[str]:
        return list(get_capability(self.platform, capability).manual_steps)
