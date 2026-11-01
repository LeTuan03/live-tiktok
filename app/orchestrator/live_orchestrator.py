"""Live Session Orchestrator — the central state machine and coordinator for autonomous livestreaming (§3, §5).

Orchestrates:
- Product playlist rotation & timing
- Realtime Avatar speech & WebRTC stream
- Dynamic 9:16 OBS compositor & browser overlays
- Anti-hallucination script generation via ProductKnowledgeService
- Comment ingestion, classification, and verified live replies
- Watchdog & error resilience
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from app.avatar_realtime.base import (
    AvatarConnectionState,
    BaseRealtimeAvatarProvider,
    RealtimeHealth,
)
from app.avatar_realtime.bridge_server import RealtimeBridgeServer
from app.comments.policy_engine import (
    CommentEventData,
    CommentPolicyEngine,
    CommentResolution,
)
from app.database.models.product import Product
from app.integrations.obs.client import OBSClient
from app.orchestrator.state_machine import LiveSessionState
from app.playout.obs_compositor import OBSCompositor
from app.services.product_knowledge_service import (
    ProductKnowledgeService,
    VerifiedProductProfile,
)


@dataclass
class LiveSessionStats:
    """Telemetry and session execution metrics."""

    session_id: str
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    products_presented: int = 0
    comments_received: int = 0
    comments_auto_replied: int = 0
    comments_sent_to_operator: int = 0
    error_count: int = 0
    reconnect_count: int = 0
    total_speech_utterances: int = 0

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return max(0.0, end - self.started_at)


class LiveSessionOrchestrator:
    """Master controller managing the lifecycle of an autonomous TikTok Live session."""

    def __init__(
        self,
        avatar_provider: BaseRealtimeAvatarProvider,
        bridge_server: RealtimeBridgeServer,
        obs_client: Optional[OBSClient] = None,
        knowledge_service: Optional[ProductKnowledgeService] = None,
        policy_engine: Optional[CommentPolicyEngine] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.avatar = avatar_provider
        self.bridge = bridge_server
        self.obs = obs_client
        self.knowledge = knowledge_service or ProductKnowledgeService()
        self.policy = policy_engine or CommentPolicyEngine(self.knowledge)
        self.compositor = OBSCompositor(self.obs) if self.obs else None

        self.session_id = session_id or f"session_{int(time.time())}"
        self.state = LiveSessionState.IDLE
        self.playlist: List[Product] = []
        self.current_product_index: int = 0
        self.current_profile: Optional[VerifiedProductProfile] = None

        self.stats = LiveSessionStats(session_id=self.session_id)
        self._is_running = False
        self._orchestrator_task: Optional[asyncio.Task] = None
        self.state_listeners: List[Callable[[LiveSessionState], None]] = []

    def add_state_listener(self, listener: Callable[[LiveSessionState], None]) -> None:
        self.state_listeners.append(listener)

    def set_state(self, new_state: LiveSessionState) -> None:
        old_state = self.state
        self.state = new_state
        logger.info("Orchestrator State Transition: {} → {}", old_state.value, new_state.value)
        for listener in self.state_listeners:
            try:
                listener(new_state)
            except Exception as e:
                logger.debug("Lỗi callback state listener: {}", e)

    def load_playlist(self, products: List[Product]) -> Tuple[int, List[str]]:
        """Load and validate products. Only verified products with facts are accepted."""
        valid_products: List[Product] = []
        rejected_reasons: List[str] = []

        for p in products:
            is_valid, errors = self.knowledge.validate_preflight(p)
            if is_valid:
                valid_products.append(p)
            else:
                rejected_reasons.append(f"Loại bỏ SKU '{p.name}': {'; '.join(errors)}")

        self.playlist = valid_products
        self.current_product_index = 0
        logger.info("Đã tải playlist: {} sản phẩm hợp lệ, {} bị từ chối", len(self.playlist), len(rejected_reasons))
        return len(self.playlist), rejected_reasons

    async def preflight(self) -> Tuple[bool, List[str]]:
        """Preflight check: OBS, Bridge, Avatar, and Playlist sanity."""
        self.set_state(LiveSessionState.PREFLIGHT)
        errors: List[str] = []

        if not self.playlist:
            errors.append("Playlist trống hoặc không có sản phẩm nào vượt qua kiểm định dữ kiện xác minh.")

        # Check Bridge server
        if not self.bridge.is_running:
            try:
                await self.bridge.start()
            except Exception as e:
                errors.append(f"Không thể khởi động Bridge Server (cổng {self.bridge.port}): {e}")

        # Check OBS connection if configured
        if self.obs:
            if not self.obs.is_connected:
                obs_ok = self.obs.connect(timeout=2.0)
                if not obs_ok:
                    logger.warning("OBS chưa kết nối (phiên vẫn có thể phát qua Bridge Server).")

        return (len(errors) == 0), errors

    async def start(self) -> bool:
        """Start the autonomous live session."""
        ok_pre, errors = await self.preflight()
        if not ok_pre:
            logger.error("Preflight thất bại: {}", errors)
            self.set_state(LiveSessionState.ERROR_FALLBACK)
            return False

        self.set_state(LiveSessionState.STARTING)
        self._is_running = True
        self.stats.started_at = time.time()

        # 1. Setup OBS Scene if OBS is connected
        if self.obs and self.obs.is_connected and self.compositor:
            try:
                self.compositor.setup_realtime_scene(
                    scene_name="LiveFlow Realtime Live",
                    bridge_host=self.bridge.host,
                    bridge_port=self.bridge.port,
                )
            except Exception as e:
                logger.warning("Không thể tự động thiết lập scene OBS: {}", e)

        # 2. Start Realtime Avatar session
        try:
            await self.avatar.start_session(
                session_id=self.session_id,
                avatar_id="mai_phuong",
                voice_id="vi-VN-HoaiMyNeural",
            )
        except Exception as e:
            logger.error("Không thể khởi động phiên Avatar Realtime: {}", e)
            self.set_state(LiveSessionState.ERROR_FALLBACK)
            return False

        # 3. Launch background execution loop
        self._orchestrator_task = asyncio.create_task(self._session_loop())
        return True

    async def pause(self) -> None:
        self.set_state(LiveSessionState.PAUSED)
        await self.avatar.interrupt_speech()

    async def resume(self) -> None:
        if self.state == LiveSessionState.PAUSED:
            self.set_state(LiveSessionState.PITCHING_SKU)

    async def stop(self) -> None:
        """Gracefully terminate live session."""
        self.set_state(LiveSessionState.STOPPING)
        self._is_running = False
        if self._orchestrator_task and not self._orchestrator_task.done():
            self._orchestrator_task.cancel()
        await self.avatar.stop_session()
        self.stats.ended_at = time.time()
        self.set_state(LiveSessionState.STOPPED)
        logger.info("Phiên live đã kết thúc. Tổng thời gian: {:.1f}s", self.stats.duration_seconds)

    async def _session_loop(self) -> None:
        """Main lifecycle state loop."""
        try:
            # 1. INTRO
            self.set_state(LiveSessionState.INTRO)
            intro_speech = (
                "Xin chào tất cả mọi người đã đến với phiên livestream chính hãng ngày hôm nay! "
                "Hôm nay shop mang đến rất nhiều ưu đãi độc quyền và voucher giảm giá cực sâu. "
                "Các bạn hãy bấm thả tim và cùng shop khám phá sản phẩm đầu tiên ngay sau đây nhé!"
            )
            await self.avatar.speak_text(intro_speech, emotion="cheerful")
            self.stats.total_speech_utterances += 1
            await asyncio.sleep(8.0)

            # 2. Product Loop
            while self._is_running and self.playlist:
                if self.state == LiveSessionState.PAUSED:
                    await asyncio.sleep(1.0)
                    continue

                product = self.playlist[self.current_product_index]
                self.current_profile = self.knowledge.extract_verified_profile(product)

                # Update OBS Overlay
                await self.bridge.broadcast_overlay_product({
                    "sku": self.current_profile.sku,
                    "name": self.current_profile.name,
                    "price": self.current_profile.price,
                    "effective_price": self.current_profile.effective_price,
                    "discount_percent": self.current_profile.discount_percent,
                    "image_url": self.current_profile.image_url,
                })

                # PITCHING_SKU
                self.set_state(LiveSessionState.PITCHING_SKU)
                pitch_segments = self._build_pitch_segments(self.current_profile)

                for seg_idx, segment in enumerate(pitch_segments):
                    if not self._is_running or self.state == LiveSessionState.PAUSED:
                        break

                    # First segment introduces product name & price: point hand to product card!
                    # Subsequent segments explain features: enthusiastic talking gestures!
                    current_gesture = "point_product" if seg_idx == 0 else "talk"
                    current_emotion = "persuasive" if seg_idx == 1 else "friendly"

                    await self.avatar.speak_text(segment, emotion=current_emotion, gesture=current_gesture)
                    self.stats.total_speech_utterances += 1
                    # Duration estimation: ~13 chars per second in Vietnamese speech
                    speech_duration = max(3.5, len(segment) / 13.0)
                    await asyncio.sleep(speech_duration + 0.5)

                self.stats.products_presented += 1

                # CALL_TO_ACTION
                if self._is_running and self.state != LiveSessionState.PAUSED:
                    self.set_state(LiveSessionState.CALL_TO_ACTION)
                    cta_speech = (
                        f"Số lượng ưu đãi cho mã {self.current_profile.name} có hạn trong phiên live này thôi ạ! "
                        "Mọi người nhanh tay bấm vào giỏ hàng góc trái bên dưới để chốt đơn ngay nha!"
                    )
                    await self.avatar.speak_text(cta_speech, emotion="persuasive", gesture="point_cart")
                    self.stats.total_speech_utterances += 1
                    await asyncio.sleep(6.0)

                # Transition to next SKU
                self.set_state(LiveSessionState.NEXT_SKU)
                self.current_product_index = (self.current_product_index + 1) % len(self.playlist)
                await asyncio.sleep(2.0)

        except asyncio.CancelledError:
            logger.info("Session loop cancelled.")
        except Exception as e:
            self.stats.error_count += 1
            logger.error("Lỗi trong session loop: {}", e)
            self.set_state(LiveSessionState.ERROR_FALLBACK)

    def _build_pitch_segments(self, profile: VerifiedProductProfile) -> List[str]:
        """Generate verified pitch segments adhering strictly to ground truth."""
        formatted_price = f"{profile.effective_price:,.0f} đồng".replace(",", ".")
        discount_text = f"đang được giảm giá sốc {profile.discount_percent}%" if profile.discount_percent > 0 else "với mức giá cực tốt"

        segments = [
            f"Và tiếp theo đây, shop xin giới thiệu đến các bạn sản phẩm {profile.name}, mã {profile.sku}.",
            f"Sản phẩm {discount_text}, chỉ còn {formatted_price} duy nhất trong buổi live hôm nay thôi ạ!",
        ]

        if profile.verified_features:
            feature_text = " và ".join(profile.verified_features[:2])
            segments.append(f"Điểm nổi bật nhất của dòng này chính là {feature_text}.")

        if profile.usage_instructions:
            clean_use = profile.usage_instructions.replace("\n", " ").strip()[:80]
            segments.append(f"Về cách sử dụng thì cực kỳ đơn giản: {clean_use}.")

        return segments

    async def handle_incoming_comment(
        self,
        username: str,
        text: str,
        source: str = "api",
        confidence: float = 1.0,
    ) -> CommentResolution:
        """Receive, sanitize, classify, and potentially speak the response live."""
        event = CommentEventData(
            username=username,
            text=text,
            source=source,
            confidence=confidence,
            session_id=self.session_id,
            product_id=self.current_profile.product_id if self.current_profile else None,
        )
        self.stats.comments_received += 1

        resolution = self.policy.evaluate(event, self.current_profile)

        if resolution.can_auto_reply and resolution.reply_text:
            self.stats.comments_auto_replied += 1
            logger.info("Phản hồi bình luận trực tiếp cho @{}: '{}'", username, resolution.reply_text)

            # 1. Show on OBS Overlay
            await self.bridge.broadcast_overlay_comment({
                "username": username,
                "text": text,
                "reply": resolution.reply_text,
            })

            # 2. Speak via Avatar Realtime without losing track of live session
            previous_state = self.state
            self.set_state(LiveSessionState.HANDLING_COMMENT)
            await self.avatar.speak_text(resolution.reply_text, emotion="friendly")
            self.stats.total_speech_utterances += 1

            # Resume session state
            self.set_state(previous_state)
        elif resolution.requires_operator:
            self.stats.comments_sent_to_operator += 1
            logger.info("Bình luận từ @{} được chuyển sang hàng chờ Người Vận Hành ({})", username, resolution.grounding_note)

        return resolution
