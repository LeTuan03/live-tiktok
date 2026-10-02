"""Base TTS Provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTTSProvider(ABC):
    """Abstract base class for TTS audio providers."""

    @abstractmethod
    async def generate_speech(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ) -> str:
        """Generate audio file from text and save to output_path."""
        pass
