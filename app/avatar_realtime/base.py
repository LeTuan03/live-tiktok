"""Base interfaces and data structures for Realtime Avatar WebRTC providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AvatarConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class RealtimeSessionInfo:
    """Active realtime streaming session metadata."""

    session_id: str
    provider_name: str
    avatar_id: str
    voice_id: str
    webrtc_url: str = ""
    room_name: str = ""
    participant_token: str = ""
    started_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RealtimeHealth:
    """Realtime health and diagnostic telemetry."""

    state: AvatarConnectionState = AvatarConnectionState.DISCONNECTED
    is_speaking: bool = False
    current_latency_ms: float = 0.0
    total_audio_seconds_played: float = 0.0
    utterances_count: int = 0
    dropped_frames: int = 0
    reconnect_count: int = 0
    last_error: str = ""
    last_heartbeat: float = field(default_factory=time.time)


class BaseRealtimeAvatarProvider(ABC):
    """Abstract base class for Realtime Avatar WebRTC stream providers (§4)."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.health = RealtimeHealth()
        self.active_session: Optional[RealtimeSessionInfo] = None

    @abstractmethod
    async def start_session(
        self,
        session_id: str,
        avatar_id: str,
        voice_id: str = "vi-VN-HoaiMyNeural",
    ) -> RealtimeSessionInfo:
        """Initialize the realtime session with the vendor or local streamer."""
        ...

    @abstractmethod
    async def speak_text(
        self,
        text: str,
        emotion: str = "friendly",
        gesture: str = "talk",
    ) -> bool:
        """Send a speech text cue to the avatar to synthesize and stream in real time."""
        ...

    @abstractmethod
    async def speak_audio(
        self,
        audio_path: str,
        transcript: str = "",
    ) -> bool:
        """Send an audio file/stream to the avatar for synchronized lip-sync."""
        ...

    @abstractmethod
    async def interrupt_speech(self) -> bool:
        """Immediately stop active speech (e.g. for high-priority comment reply)."""
        ...

    @abstractmethod
    async def stop_session(self) -> bool:
        """Gracefully disconnect and tear down the streaming session."""
        ...

    def get_health(self) -> RealtimeHealth:
        """Return the current diagnostic health snapshot."""
        return self.health
