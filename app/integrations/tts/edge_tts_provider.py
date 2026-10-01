"""Edge TTS Provider implementation using edge-tts library."""

from __future__ import annotations

import os
from typing import List, Dict, Any
import edge_tts

from loguru import logger
from app.integrations.tts.base import BaseTTSProvider


class EdgeTTSProvider(BaseTTSProvider):
    """TTS provider powered by Microsoft Edge Web TTS service."""

    DEFAULT_VOICES = [
        {"id": "vi-VN-HoaiMyNeural", "name": "Hoài My (Nữ - Việt Nam)", "gender": "Female", "lang": "vi-VN"},
        {"id": "vi-VN-NamMinhNeural", "name": "Nam Minh (Nam - Việt Nam)", "gender": "Male", "lang": "vi-VN"},
        {"id": "en-US-AvaNeural", "name": "Ava (Female - US)", "gender": "Female", "lang": "en-US"},
        {"id": "en-US-AndrewNeural", "name": "Andrew (Male - US)", "gender": "Male", "lang": "en-US"},
    ]

    async def generate_speech(
        self,
        text: str,
        output_path: str,
        voice_id: str = "vi-VN-HoaiMyNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ) -> str:
        """Generate audio file from text using edge-tts."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        logger.info(f"Đang phát giọng đọc Edge TTS: voice={voice_id}, text len={len(text)}")
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        await communicate.save(output_path)
        logger.info(f"Đã tạo file âm thanh TTS thành công: {output_path}")
        return output_path

    @staticmethod
    async def list_available_voices() -> List[Dict[str, Any]]:
        """List available voices from Edge TTS service."""
        try:
            voices = await edge_tts.list_voices()
            return [
                {
                    "id": v["ShortName"],
                    "name": f"{v['FriendlyName']} ({v['Gender']})",
                    "gender": v["Gender"],
                    "lang": v["Locale"],
                }
                for v in voices
                if v["Locale"].startswith("vi") or v["Locale"].startswith("en")
            ]
        except Exception as e:
            logger.warning(f"Không thể lấy danh sách giọng từ Edge TTS server ({e}), trả về danh sách mặc định.")
            return EdgeTTSProvider.DEFAULT_VOICES
