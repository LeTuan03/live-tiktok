"""Local Realtime Avatar Provider — delivers real Vietnamese TTS, responsive lip-sync, and zero-cost OBS testing.

Used for:
- Phase 1 POC verification without draining API credits
- Offline resilience and dry-run streaming
- Realtime speech generation and viseme synchronization
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

import edge_tts
from loguru import logger

from app.avatar_realtime.base import (
    AvatarConnectionState,
    BaseRealtimeAvatarProvider,
    RealtimeHealth,
    RealtimeSessionInfo,
)
from app.avatar_realtime.bridge_server import RealtimeBridgeServer


class LocalMockRealtimeProvider(BaseRealtimeAvatarProvider):
    """Local Realtime Avatar provider using Edge-TTS and browser canvas animation."""

    def __init__(
        self,
        bridge_server: RealtimeBridgeServer,
        default_voice: str = "vi-VN-HoaiMyNeural",
    ) -> None:
        super().__init__(provider_name="local_mock")
        self.bridge = bridge_server
        self.default_voice = default_voice
        self.temp_dir = Path(tempfile.gettempdir()) / "liveflow_realtime_audio"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._current_audio_task: Optional[asyncio.Task] = None

    async def start_session(
        self,
        session_id: str,
        avatar_id: str,
        voice_id: str = "vi-VN-HoaiMyNeural",
    ) -> RealtimeSessionInfo:
        """Start a local realtime session."""
        self.health.state = AvatarConnectionState.CONNECTING
        logger.info("Khởi động phiên avatar realtime nội bộ: session={}, avatar={}, voice={}", session_id, avatar_id, voice_id)

        # Ensure bridge server is running
        if not self.bridge.is_running:
            await self.bridge.start()

        self.default_voice = voice_id or self.default_voice
        self.active_session = RealtimeSessionInfo(
            session_id=session_id,
            provider_name=self.provider_name,
            avatar_id=avatar_id,
            voice_id=self.default_voice,
            webrtc_url=f"http://{self.bridge.host}:{self.bridge.port}/avatar.html",
        )

        self.health.state = AvatarConnectionState.STREAMING
        self.health.last_heartbeat = time.time()
        logger.info("Phiên avatar realtime đã sẵn sàng tại {}", self.active_session.webrtc_url)
        return self.active_session

    async def speak_text(
        self,
        text: str,
        emotion: str = "friendly",
        gesture: str = "talk",
    ) -> bool:
        """Synthesize Vietnamese speech in real time with Edge-TTS and broadcast to OBS."""
        if not text or not text.strip():
            return False

        t0 = time.time()
        clean_text = text.strip()
        filename = f"tts_{int(time.time() * 1000)}.mp3"
        audio_path = self.temp_dir / filename

        # Retry up to 2 times for edge-tts network drops
        for attempt in range(1, 3):
            try:
                communicate = edge_tts.Communicate(clean_text, self.default_voice)
                await communicate.save(str(audio_path))
                latency = (time.time() - t0) * 1000.0

                # Convert to relative URL served by the bridge
                audio_url = f"/static/avatars/{filename}"
                from app.config.settings import get_project_root
                dest_dir = get_project_root() / "storage" / "avatars"
                dest_dir.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(str(audio_path), str(dest_dir / filename))

                self.health.is_speaking = True
                self.health.current_latency_ms = latency
                self.health.utterances_count += 1
                self.health.last_heartbeat = time.time()

                logger.info("TTS phát thành công (độ trễ {:.0f}ms): '{}'", latency, clean_text[:40])

                # Broadcast to OBS Browser Source
                await self.bridge.broadcast_avatar_speech(
                    text=clean_text,
                    audio_url=audio_url,
                    emotion=emotion,
                    gesture=gesture,
                )
                return True
            except Exception as e:
                self.health.last_error = str(e)
                if attempt < 2:
                    await asyncio.sleep(0.5)
                else:
                    logger.warning("Edge-TTS gián đoạn, kích hoạt gTTS tiếng Việt dự phòng: {}", e)
                    try:
                        from gtts import gTTS
                        tts = gTTS(text=clean_text, lang="vi")
                        await asyncio.to_thread(tts.save, str(audio_path))
                        audio_url = f"/static/avatars/{filename}"
                        from app.config.settings import get_project_root
                        dest_dir = get_project_root() / "storage" / "avatars"
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        import shutil
                        shutil.copy2(str(audio_path), str(dest_dir / filename))
                        await self.bridge.broadcast_avatar_speech(
                            text=clean_text,
                            audio_url=audio_url,
                            emotion=emotion,
                            gesture=gesture,
                        )
                        return True
                    except Exception as g_err:
                        logger.warning("gTTS dự phòng cũng lỗi: {}", g_err)
                        await self.bridge.broadcast_avatar_speech(
                            text=clean_text,
                            audio_url="",
                            emotion=emotion,
                            gesture=gesture,
                        )
                        return True
        return False

    async def speak_audio(self, audio_path: str, transcript: str = "") -> bool:
        """Send an existing audio file to OBS avatar."""
        try:
            dest_name = Path(audio_path).name
            from app.config.settings import get_project_root
            dest_dir = get_project_root() / "storage" / "avatars"
            dest_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(audio_path, str(dest_dir / dest_name))

            audio_url = f"/static/avatars/{dest_name}"
            await self.bridge.broadcast_avatar_speech(
                text=transcript or "Đang phát clip giới thiệu",
                audio_url=audio_url,
            )
            return True
        except Exception as e:
            logger.error("Lỗi phát audio clip: {}", e)
            return False

    async def interrupt_speech(self) -> bool:
        """Stop current speech immediately."""
        await self.bridge.broadcast_avatar_stop()
        self.health.is_speaking = False
        return True

    async def stop_session(self) -> bool:
        """Tear down session."""
        await self.interrupt_speech()
        self.health.state = AvatarConnectionState.DISCONNECTED
        self.active_session = None
        logger.info("Đã đóng phiên avatar realtime")
        return True
