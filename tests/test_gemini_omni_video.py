"""Unit tests for Gemini Omni video generation and alignment workflow."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.veo.veo_client import VeoClient
from app.render.veo_stitcher import VeoStitcher


class TestVeoClientOmni:
    def test_default_model_is_omni(self) -> None:
        client = VeoClient(api_key="AIzaSy_fake_test_key_1234567890abcdef")
        assert client.model_name == "gemini-omni-1.1-flash"
        assert client.DEFAULT_MODEL == "gemini-omni-1.1-flash"

    def test_find_video_item_from_nested_response(self) -> None:
        # Standard Steps structure
        resp = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "video",
                            "mime_type": "video/mp4",
                            "uri": "https://generativelanguage.googleapis.com/v1beta/files/test_file_99:download?alt=media",
                        }
                    ],
                }
            ]
        }
        item = VeoClient._find_video_item(resp)
        assert item is not None
        assert item.get("uri") == "https://generativelanguage.googleapis.com/v1beta/files/test_file_99:download?alt=media"

    def test_find_video_item_inline_data(self) -> None:
        resp = {
            "output": {
                "type": "video",
                "data": "AAAAIGZ0eXBpc29t...",
            }
        }
        item = VeoClient._find_video_item(resp)
        assert item is not None
        assert item.get("data") == "AAAAIGZ0eXBpc29t..."

    def test_file_id_from_uri(self) -> None:
        uri1 = "https://generativelanguage.googleapis.com/v1beta/files/abc123xyz:download?alt=media"
        assert VeoClient._file_id_from_uri(uri1) == "abc123xyz"

        uri2 = "https://generativelanguage.googleapis.com/v1beta/files/my_video_id?alt=media"
        assert VeoClient._file_id_from_uri(uri2) == "my_video_id"

        uri3 = "https://storage.googleapis.com/some_other_path"
        assert VeoClient._file_id_from_uri(uri3) == ""

    def test_resolve_api_key_explicit(self) -> None:
        resolved = VeoClient._resolve_api_key("AIzaSy_explicit_key_1234567890")
        assert resolved == "AIzaSy_explicit_key_1234567890"

    def test_resolve_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIVEFLOW_GEMINI_API_KEY", "AIzaSy_env_key_1234567890abcdef")
        resolved = VeoClient._resolve_api_key("")
        assert resolved == "AIzaSy_env_key_1234567890abcdef"

    @pytest.mark.asyncio
    async def test_generate_video_dispatches_to_omni(self) -> None:
        client = VeoClient(api_key="AIzaSy_test_valid_key_1234567890")
        with patch.object(client, "_generate_omni_video", new_callable=AsyncMock) as mock_omni:
            mock_omni.return_value = "/tmp/fake_omni.mp4"
            res = await client.generate_video(prompt="A presenter speaking")
            assert res == "/tmp/fake_omni.mp4"
            mock_omni.assert_called_once()

    @pytest.mark.asyncio
    async def test_deprecated_model_raises_clear_error_when_fallback_disabled(self) -> None:
        client = VeoClient(
            api_key="AIzaSy_test_valid_key_1234567890",
            model_name="veo-3.1-fast-generate-preview",
        )
        with pytest.raises(RuntimeError) as exc_info:
            await client.generate_video(prompt="Test deprecated")
        assert "không còn khả dụng" in str(exc_info.value)


class TestVeoStitcherOmni:
    def test_has_audio_stream_with_mock_probe(self) -> None:
        stitcher = VeoStitcher()
        mock_result = MagicMock()
        mock_result.has_audio = True
        mock_result.raw = {"streams": [{"codec_type": "audio"}]}

        with patch.object(stitcher.ffprobe, "probe", return_value=mock_result):
            assert stitcher.has_audio_stream("test.mp4") is True

    def test_has_audio_stream_no_audio(self) -> None:
        stitcher = VeoStitcher()
        mock_result = MagicMock()
        mock_result.has_audio = False
        mock_result.raw = {"streams": [{"codec_type": "video"}]}

        with patch.object(stitcher.ffprobe, "probe", return_value=mock_result):
            assert stitcher.has_audio_stream("test.mp4") is False
