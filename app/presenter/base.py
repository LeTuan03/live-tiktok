"""Base interfaces and data structures for AI Presenter Providers and gesture clips."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PresenterClip:
    """A gesture or presentation video clip for an AI presenter identity."""

    cue: str  # One of standard 9 cues: WAVE_GREET, HOLD_PRODUCT, etc.
    file_path: str
    duration: float = 0.0
    has_product: bool = False
    product_id: Optional[int] = None
    is_approved: bool = False  # §4.2: Bắt buộc duyệt tay trước khi phát sóng
    transparent_bg: bool = True  # True = green screen (chroma key)
    chroma_color: str = "#00FF00"
    preview_path: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PresenterClip:
        return cls(**data)


class PresenterProvider(ABC):
    """Abstract base class for pluggable AI presenter engines (Cloud Lipsync, I2V, etc.)."""

    def __init__(self, name: str, provider_type: str = "cloud_lipsync") -> None:
        self.name = name
        self.provider_type = provider_type  # 'cloud_lipsync', 'i2v', 'cloud_full'

    @abstractmethod
    async def generate_lipsync(
        self,
        base_clip: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
    ) -> Path:
        """Apply lipsync matching the audio track to the presenter's base gesture clip."""
        pass

    @abstractmethod
    async def generate_gesture_clip(
        self,
        reference_image: Path | str,
        cue: str,
        prompt: str,
        output_path: Path | str,
    ) -> Path:
        """Generate an image-to-video gesture clip for a given cue from presenter identity photo."""
        pass

    @abstractmethod
    async def generate_product_clip(
        self,
        presenter_image: Path | str,
        product_image: Path | str,
        cue: str,
        output_path: Path | str,
        prompt_override: Optional[str] = None,
    ) -> Path:
        """Generate a clip with the presenter holding or pointing to the specific product."""
        pass

    @abstractmethod
    def validate_credentials(self) -> tuple[bool, str]:
        """Verify provider API keys or configuration."""
        pass
