"""Realtime Avatar Module — WebRTC streaming, OBS Browser Source integration, and dynamic speech synthesis."""

from app.avatar_realtime.base import (
    AvatarConnectionState,
    BaseRealtimeAvatarProvider,
    RealtimeHealth,
    RealtimeSessionInfo,
)
from app.avatar_realtime.bridge_server import RealtimeBridgeServer
from app.avatar_realtime.factory import create_realtime_avatar_provider
from app.avatar_realtime.liveavatar_provider import LiveAvatarLiteProvider
from app.avatar_realtime.local_mock_provider import LocalMockRealtimeProvider

__all__ = [
    "AvatarConnectionState",
    "BaseRealtimeAvatarProvider",
    "RealtimeHealth",
    "RealtimeSessionInfo",
    "RealtimeBridgeServer",
    "LiveAvatarLiteProvider",
    "LocalMockRealtimeProvider",
    "create_realtime_avatar_provider",
]
