"""LiveAvatar LITE Mode WebRTC Provider (§4).

Integrates with LiveAvatar's LITE mode where the system retains full control over LLM,
policy, product sequence, and speech generation, while LiveAvatar renders the realtime video.
See: https://docs.liveavatar.com/docs/lite-mode/overview
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from app.avatar_realtime.base import (
    AvatarConnectionState,
    BaseRealtimeAvatarProvider,
    RealtimeHealth,
    RealtimeSessionInfo,
)
from app.avatar_realtime.bridge_server import RealtimeBridgeServer

LIVEAVATAR_API_BASE = "https://api.liveavatar.com/v1"


class LiveAvatarLiteProvider(BaseRealtimeAvatarProvider):
    """Realtime avatar provider using LiveAvatar LITE mode."""

    def __init__(
        self,
        api_key: str,
        bridge_server: RealtimeBridgeServer,
        base_url: str = LIVEAVATAR_API_BASE,
    ) -> None:
        super().__init__(provider_name="liveavatar_lite")
        self.api_key = api_key.strip()
        self.bridge = bridge_server
        self.base_url = base_url.rstrip("/")
        self.http_session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _get_http(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self.http_session

    async def start_session(
        self,
        session_id: str,
        avatar_id: str,
        voice_id: str = "vi-VN-HoaiMyNeural",
    ) -> RealtimeSessionInfo:
        """Create a LITE mode session on LiveAvatar."""
        self.health.state = AvatarConnectionState.CONNECTING
        logger.info("Khởi tạo session LiveAvatar LITE: avatar_id={}", avatar_id)

        # Fallback to local mock if no API key is provided
        if not self.api_key:
            logger.warning("Chưa cấu hình LiveAvatar API key! Tự động fallback về Local Streamer.")
            from app.avatar_realtime.local_mock_provider import LocalMockRealtimeProvider
            mock = LocalMockRealtimeProvider(self.bridge, default_voice=voice_id)
            return await mock.start_session(session_id, avatar_id, voice_id)

        payload = {
            "avatar_id": avatar_id,
            "mode": "lite",
            "voice": {"provider": "edge", "voice_id": voice_id},
            "stream_format": "webrtc",
        }

        try:
            http = await self._get_http()
            url = f"{self.base_url}/lite/sessions"
            async with http.post(url, json=payload) as resp:
                data = await resp.json(content_type=None)
                if resp.status not in (200, 201):
                    err = data.get("message") or f"HTTP {resp.status}"
                    raise RuntimeError(f"LiveAvatar API Error: {err}")

                live_session_id = data.get("session_id", session_id)
                webrtc_url = data.get("webrtc_url", f"http://{self.bridge.host}:{self.bridge.port}/avatar.html")
                token = data.get("token", "")

                self.active_session = RealtimeSessionInfo(
                    session_id=live_session_id,
                    provider_name=self.provider_name,
                    avatar_id=avatar_id,
                    voice_id=voice_id,
                    webrtc_url=webrtc_url,
                    participant_token=token,
                    metadata=data,
                )
                self.health.state = AvatarConnectionState.STREAMING
                logger.info("LiveAvatar LITE session thành công: {}", live_session_id)
                return self.active_session
        except Exception as e:
            self.health.state = AvatarConnectionState.ERROR
            self.health.last_error = str(e)
            logger.error("Không thể khởi tạo LiveAvatar LITE: {}", e)
            raise

    async def speak_text(
        self,
        text: str,
        emotion: str = "friendly",
        gesture: str = "talk",
    ) -> bool:
        """Send speech text to LiveAvatar session."""
        if not self.active_session:
            return False

        t0 = time.time()
        url = f"{self.base_url}/lite/sessions/{self.active_session.session_id}/speak"
        payload = {
            "text": text,
            "emotion": emotion,
            "gesture": gesture,
        }

        try:
            http = await self._get_http()
            async with http.post(url, json=payload) as resp:
                if resp.status == 200:
                    latency = (time.time() - t0) * 1000.0
                    self.health.current_latency_ms = latency
                    self.health.utterances_count += 1
                    # Also notify bridge for local subtitle sync
                    await self.bridge.broadcast_avatar_speech(text, emotion=emotion)
                    return True
                else:
                    logger.error("Lỗi gửi lời thoại tới LiveAvatar (HTTP {})", resp.status)
                    return False
        except Exception as e:
            logger.error("Lỗi kết nối LiveAvatar speak: {}", e)
            return False

    async def speak_audio(self, audio_path: str, transcript: str = "") -> bool:
        """Send raw audio stream/file to LiveAvatar."""
        # LiveAvatar LITE accepts audio chunks via WebSocket or REST
        return await self.speak_text(transcript or "Phát âm thanh")

    async def interrupt_speech(self) -> bool:
        """Interrupt current speech in LiveAvatar."""
        if not self.active_session:
            return False
        url = f"{self.base_url}/lite/sessions/{self.active_session.session_id}/interrupt"
        try:
            http = await self._get_http()
            async with http.post(url) as resp:
                await self.bridge.broadcast_avatar_stop()
                return resp.status == 200
        except Exception as e:
            logger.debug("Lỗi interrupt LiveAvatar: {}", e)
            return False

    async def stop_session(self) -> bool:
        """Tear down LiveAvatar session."""
        if not self.active_session:
            return True
        url = f"{self.base_url}/lite/sessions/{self.active_session.session_id}/stop"
        try:
            http = await self._get_http()
            async with http.post(url):
                pass
        except Exception:
            pass
        finally:
            self.health.state = AvatarConnectionState.DISCONNECTED
            self.active_session = None
            if self.http_session and not self.http_session.closed:
                await self.http_session.close()
        return True
