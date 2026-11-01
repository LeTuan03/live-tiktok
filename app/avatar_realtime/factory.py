"""Factory for creating Realtime Avatar WebRTC providers."""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from app.avatar_realtime.base import BaseRealtimeAvatarProvider
from app.avatar_realtime.bridge_server import RealtimeBridgeServer
from app.avatar_realtime.liveavatar_provider import LiveAvatarLiteProvider
from app.avatar_realtime.local_mock_provider import LocalMockRealtimeProvider


def create_realtime_avatar_provider(
    provider_type: Optional[str] = None,
    api_key: Optional[str] = None,
    bridge_server: Optional[RealtimeBridgeServer] = None,
    default_voice: str = "vi-VN-HoaiMyNeural",
) -> BaseRealtimeAvatarProvider:
    """Factory creating the appropriate Realtime Avatar provider.

    Options:
    - 'local_mock' / 'local': Uses Edge-TTS + OBS Browser Source (zero-cost, offline POC).
    - 'liveavatar' / 'liveavatar_lite': LiveAvatar LITE mode WebRTC streaming.
    """
    bridge = bridge_server or RealtimeBridgeServer()
    ptype = (provider_type or os.environ.get("LIVEFLOW_REALTIME_AVATAR_PROVIDER", "local_mock")).lower().strip()

    if ptype in ("liveavatar", "liveavatar_lite"):
        key = api_key or os.environ.get("LIVEFLOW_LIVEAVATAR_API_KEY", "")
        logger.info("Khởi tạo Realtime Avatar: LiveAvatar LITE Mode")
        return LiveAvatarLiteProvider(api_key=key, bridge_server=bridge)
    else:
        logger.info("Khởi tạo Realtime Avatar: Local Streamer (Edge-TTS + OBS Browser Source)")
        return LocalMockRealtimeProvider(bridge_server=bridge, default_voice=default_voice)
