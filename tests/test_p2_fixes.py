"""Unit tests for Phase 2 (P2) implementations:
- Segmented Script Studio (JSON schema, validator, cue & shot normalization)
- Scene Sets & Library (floor_y, anchor_x, product_zone, depth-of-field)
- Review Rhythm Shot Planner (8-15s cuts, max 20s, no consecutive identical shots, closeup fallback)
- VideoCompositor v1 (1080x1920 MP4 offline review rendering, verification)
- Structured TTS synthesis with ffprobe real duration
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.database.models.voice import GeneratedAudio, VoiceProfile
from app.render.compositor import VideoCompositor
from app.render.shot_planner import PlannedShot, ShotPlan, ShotPlanner
from app.services.script_segment_service import (
    ScriptSegment,
    StructuredScript,
    fallback_parse_raw_text,
    normalize_cue,
    normalize_shot,
    parse_and_validate_script_json,
)
from app.services.voice_service import VoiceService
from app.sets.library import SceneSetLibrary
from app.sets.scene_set import SceneSet


# =============================================================================
# 1. SCRIPT SEGMENTATION & JSON VALIDATOR TESTS
# =============================================================================
class TestScriptSegmentation:
    def test_parse_valid_json_segments(self) -> None:
        raw_json = json.dumps({
            "set_id": "bep_sang",
            "segments": [
                {
                    "text": "Chào cả nhà đã đến với phiên LIVE hôm nay!",
                    "cue": "WAVE_GREET",
                    "shot": "WIDE_MODEL",
                    "emphasis": "high",
                },
                {
                    "text": "Trên tay em là chiếc chảo chống dính vân đá thế hệ mới.",
                    "cue": "HOLD_PRODUCT",
                    "shot": "MEDIUM_MODEL",
                    "emphasis": "normal",
                },
            ]
        })
        structured = parse_and_validate_script_json(raw_json)
        assert structured.set_id == "bep_sang"
        assert len(structured.segments) == 2
        assert structured.segments[0].cue == "WAVE_GREET"
        assert structured.segments[0].shot == "WIDE_MODEL"
        assert structured.segments[1].cue == "HOLD_PRODUCT"

    def test_stage_directions_stripped_from_segments(self) -> None:
        raw_json = json.dumps({
            "set_id": "phong_khach_am",
            "segments": [
                {
                    "text": "Chào mọi người (cười tươi rạng rỡ), xem deal này [giơ sản phẩm lên]!",
                    "cue": "WAVE_GREET",
                    "shot": "WIDE_MODEL",
                }
            ]
        })
        structured = parse_and_validate_script_json(raw_json)
        clean_text = structured.segments[0].clean_text_for_tts()
        assert "(cười tươi rạng rỡ)" not in clean_text
        assert "[giơ sản phẩm lên]" not in clean_text
        assert "Chào mọi người" in clean_text
        assert "xem deal này" in clean_text

    def test_normalize_cue_and_shot(self) -> None:
        assert normalize_cue("chào mừng") == "WAVE_GREET"
        assert normalize_cue("cầm trên tay") == "HOLD_PRODUCT"
        assert normalize_cue("soi cận cảnh") == "SHOW_DETAIL"
        assert normalize_cue("giá sốc hôm nay") == "POINT_PRICE"
        assert normalize_cue("bấm vào giỏ hàng") == "CTA_POINT_DOWN"
        assert normalize_cue("unknown_xyz") == "IDLE_TALK"

        assert normalize_shot("toàn cảnh") == "WIDE_MODEL"
        assert normalize_shot("cận cảnh chi tiết") == "CLOSEUP_PRODUCT"
        assert normalize_shot("bảng chia đôi") == "SPLIT"
        assert normalize_shot("ô nhỏ góc") == "INSET_PRODUCT"
        assert normalize_shot("unknown_xyz") == "MEDIUM_MODEL"

    def test_fallback_parse_raw_text(self) -> None:
        raw_text = (
            "Chào mừng mọi người đến với phiên livestream đồ gia dụng thông minh.\n\n"
            "Hôm nay em giới thiệu bộ nồi inox 5 đáy cực bền đẹp.\n\n"
            "Chất liệu inox 304 cao cấp không gỉ, tay cầm cách nhiệt chống bỏng.\n\n"
            "Giá gốc 890k hôm nay trên LIVE chỉ còn 499k kèm quà tặng.\n\n"
            "Nhanh tay bấm vào giỏ hàng góc trái màn hình để nhận ưu đãi ngay nhé!"
        )
        structured = parse_and_validate_script_json(raw_text, default_set_id="bep_sang")
        assert structured.set_id == "bep_sang"
        assert len(structured.segments) >= 4
        # First segment should be greeting
        assert structured.segments[0].cue == "WAVE_GREET"
        assert structured.segments[0].shot == "WIDE_MODEL"
        # Last segment should be CTA
        assert structured.segments[-1].cue == "CTA_POINT_DOWN"
        assert structured.segments[-1].shot == "MEDIUM_MODEL"

    def test_markdown_codeblock_json_is_stripped(self) -> None:
        fenced_json = """```json
{
  "set_id": "studio_den_neon",
  "segments": [
    {
      "text": "Tai nghe bluetooth gaming cực chất.",
      "cue": "HOLD_PRODUCT",
      "shot": "MEDIUM_MODEL"
    }
  ]
}
```"""
        structured = parse_and_validate_script_json(fenced_json)
        assert structured.set_id == "studio_den_neon"
        assert len(structured.segments) == 1
        assert structured.segments[0].shot == "MEDIUM_MODEL"


# =============================================================================
# 2. SCENE SETS & LIBRARY TESTS
# =============================================================================
class TestSceneSets:
    def test_scene_set_dataclass_properties(self) -> None:
        s = SceneSet(
            id="test_set",
            name="Test Studio",
            category="electronics",
            background="/path/to/bg.png",
            floor_y=1500,
            anchor_x=540,
            product_zone=(100, 1100, 450, 1400),
            bg_blur=3.0,
            shadow_alpha=0.4,
        )
        d = s.to_dict()
        assert d["id"] == "test_set"
        assert d["floor_y"] == 1500
        assert d["product_zone"] == (100, 1100, 450, 1400)

    def test_library_initializes_six_default_sets(self, tmp_path: Path) -> None:
        lib = SceneSetLibrary(storage_dir=tmp_path)
        all_sets = lib.get_all()
        assert len(all_sets) >= 6

        expected_ids = {
            "phong_khach_am",
            "bep_sang",
            "quay_my_pham_trang",
            "studio_den_neon",
            "shop_thoi_trang",
            "studio_gradient",
        }
        actual_ids = {s.id for s in all_sets}
        assert expected_ids.issubset(actual_ids)

        for s in all_sets:
            assert Path(s.background).exists()
            assert 1000 <= s.floor_y <= 1800
            assert len(s.product_zone) == 4

    def test_scene_set_lookup_and_fallback(self, tmp_path: Path) -> None:
        lib = SceneSetLibrary(storage_dir=tmp_path)
        bep = lib.get_set("bep_sang")
        assert bep is not None
        assert "Bếp" in bep.name

        # Unknown id should gracefully fallback to default
        fallback = lib.get_set_or_default("unknown_nonexistent_set")
        assert fallback is not None
        assert fallback.id == "studio_gradient"


# =============================================================================
# 3. REVIEW RHYTHM SHOT PLANNER TESTS
# =============================================================================
class TestShotPlanner:
    def test_shot_planner_generates_valid_timeline(self) -> None:
        planner = ShotPlanner()
        segments = [
            ScriptSegment("Xin chào cả nhà!", "WAVE_GREET", "WIDE_MODEL", duration_seconds=8.0),
            ScriptSegment("Hôm nay có chiếc nồi chiên siêu tiện lợi.", "HOLD_PRODUCT", "MEDIUM_MODEL", duration_seconds=12.0),
            ScriptSegment("Lòng nồi phủ men chống dính cao cấp dễ vệ sinh.", "SHOW_DETAIL", "CLOSEUP_PRODUCT", duration_seconds=10.0),
            ScriptSegment("Giá sốc hôm nay giảm chỉ còn 499k nhé cả nhà.", "POINT_PRICE", "SPLIT", duration_seconds=10.0),
            ScriptSegment("Bấm vào góc trái màn hình nhận voucher ngay!", "CTA_POINT_DOWN", "MEDIUM_MODEL", duration_seconds=8.0),
        ]
        structured = StructuredScript(set_id="bep_sang", segments=segments)
        plan = planner.plan_shots(structured, has_closeup_asset=True)

        assert plan.total_duration == pytest.approx(48.0, rel=1e-2)
        assert len(plan.shots) >= 4

    def test_shot_planner_caps_max_duration_at_20s(self) -> None:
        planner = ShotPlanner()
        # Single long segment of 35 seconds
        segments = [
            ScriptSegment("Đoạn thoại siêu dài giải thích tính năng kỹ thuật chi tiết của sản phẩm.", "IDLE_TALK", "MEDIUM_MODEL", duration_seconds=35.0),
        ]
        structured = StructuredScript(segments=segments)
        plan = planner.plan_shots(structured, has_closeup_asset=True)

        # Must have split the 35s segment into multiple shots so no single shot > 20s
        for shot in plan.shots:
            assert shot.duration <= 20.01

    def test_shot_planner_no_consecutive_identical_shots(self) -> None:
        planner = ShotPlanner()
        segments = [
            ScriptSegment("Đoạn 1", "HOLD_PRODUCT", "MEDIUM_MODEL", duration_seconds=10.0),
            ScriptSegment("Đoạn 2", "HOLD_PRODUCT", "MEDIUM_MODEL", duration_seconds=10.0),
            ScriptSegment("Đoạn 3", "HOLD_PRODUCT", "MEDIUM_MODEL", duration_seconds=10.0),
        ]
        structured = StructuredScript(segments=segments)
        plan = planner.plan_shots(structured, has_closeup_asset=True)

        for i in range(len(plan.shots) - 1):
            assert plan.shots[i].shot != plan.shots[i + 1].shot

    def test_shot_planner_downgrades_closeup_when_no_asset(self) -> None:
        planner = ShotPlanner()
        segments = [
            ScriptSegment("Xem cận cảnh nhãn này.", "SHOW_DETAIL", "CLOSEUP_PRODUCT", duration_seconds=10.0),
        ]
        structured = StructuredScript(segments=segments)
        # Without closeup asset
        plan = planner.plan_shots(structured, has_closeup_asset=False)

        assert len(plan.shots) > 0
        for shot in plan.shots:
            assert shot.shot != "CLOSEUP_PRODUCT"
            # Auto-downgraded to INSET_PRODUCT or MEDIUM_MODEL
            assert shot.shot in ("INSET_PRODUCT", "MEDIUM_MODEL")

    def test_shot_planner_minimum_shot_changes(self) -> None:
        planner = ShotPlanner()
        segments = [
            ScriptSegment("Đoạn 1", duration_seconds=8.0),
            ScriptSegment("Đoạn 2", duration_seconds=10.0),
            ScriptSegment("Đoạn 3", duration_seconds=10.0),
            ScriptSegment("Đoạn 4", duration_seconds=10.0),
        ]
        structured = StructuredScript(segments=segments)
        plan = planner.plan_shots(structured, has_closeup_asset=True)
        # Requirement §4.4: >= 3 shot changes
        assert len(plan.shots) >= 3


# =============================================================================
# 4. VIDEO COMPOSITOR V1 TESTS
# =============================================================================
class TestVideoCompositorV1:
    def test_prepare_background_frame(self, tmp_path: Path) -> None:
        compositor = VideoCompositor()
        scene_set = SceneSet(
            id="test_set",
            name="Test",
            category="all",
            background="",
            bg_blur=2.0,
            shadow_alpha=0.3,
            product_zone=(100, 1000, 500, 1400),
        )

        # Create dummy product image
        prod_img_path = tmp_path / "product.png"
        img = Image.new("RGBA", (200, 200), (255, 100, 50, 255))
        img.save(str(prod_img_path))

        out_frame = tmp_path / "composite_bg.png"
        compositor._prepare_background_frame(
            output_path=out_frame,
            scene_set=scene_set,
            product_image=prod_img_path,
        )

        assert out_frame.exists()
        res = Image.open(str(out_frame))
        assert res.size == (1080, 1920)

    def test_verify_rendered_video_criteria(self, tmp_path: Path) -> None:
        compositor = VideoCompositor()

        # Mock probe result for a valid vertical video
        valid_probe = MagicMock()
        valid_probe.width = 1080
        valid_probe.height = 1920
        valid_probe.codec = "h264"
        valid_probe.is_video = True
        valid_probe.duration = 45.0
        valid_probe.to_dict.return_value = {"width": 1080, "height": 1920}
        valid_probe.raw = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        with patch.object(compositor.ffprobe, "probe", return_value=valid_probe):
            check = compositor.verify_rendered_video(tmp_path / "mock.mp4", expected_duration=45.0)
            assert check["valid"] is True
            assert len(check["errors"]) == 0

        # Mock invalid resolution (horizontal instead of vertical)
        invalid_probe = MagicMock()
        invalid_probe.width = 1920
        invalid_probe.height = 1080
        invalid_probe.codec = "h264"
        invalid_probe.is_video = True
        invalid_probe.duration = 45.0
        invalid_probe.to_dict.return_value = {"width": 1920, "height": 1080}
        invalid_probe.raw = {"streams": [{"codec_type": "video"}]}

        with patch.object(compositor.ffprobe, "probe", return_value=invalid_probe):
            check = compositor.verify_rendered_video(tmp_path / "mock.mp4", expected_duration=45.0)
            assert check["valid"] is False
            assert any("1080×1920" in e for e in check["errors"])
            assert any("âm thanh" in e for e in check["errors"])


# =============================================================================
# 5. STRUCTURED TTS GENERATION TESTS
# =============================================================================
class TestStructuredTTS:
    def test_synthesize_structured_script_updates_segment_durations(self, tmp_path: Path) -> None:
        import asyncio

        mock_repo = MagicMock()
        mock_repo.list_profiles.return_value = [
            VoiceProfile(id=1, name="Hoài My", voice_id="vi-VN-HoaiMyNeural", speed=1.0, is_default=True)
        ]
        mock_repo.get_default_profile.return_value = mock_repo.list_profiles.return_value[0]

        mock_audio = GeneratedAudio(
            id=10,
            script_id=99,
            file_path=str(tmp_path / "master.mp3"),
            content_hash="mockhash",
            duration_seconds=22.5,
        )
        mock_repo.create_generated_audio.return_value = mock_audio

        service = VoiceService(voice_repo=mock_repo, storage_dir=tmp_path)

        # Mock synthesis: creates fake mp3 files
        async def fake_synthesize(text, out_path, voice_id, speed, volume):
            with open(out_path, "wb") as f:
                f.write(b"FAKE_MP3_FRAME" * 200)

        service._synthesize = AsyncMock(side_effect=fake_synthesize)
        service.get_audio_duration = MagicMock(return_value=7.5)

        segments = [
            ScriptSegment("Đoạn một chào hỏi.", "WAVE_GREET", "WIDE_MODEL"),
            ScriptSegment("Đoạn hai giới thiệu sản phẩm.", "HOLD_PRODUCT", "MEDIUM_MODEL"),
            ScriptSegment("Đoạn ba chốt đơn.", "CTA_POINT_DOWN", "MEDIUM_MODEL"),
        ]
        structured = StructuredScript(set_id="bep_sang", segments=segments)

        audio, updated = asyncio.run(
            service.synthesize_structured_script(
                script_id=99,
                structured_script=structured,
                force_regenerate=True,
            )
        )

        assert audio is not None
        assert len(updated.segments) == 3
        # Each segment duration should have been probed and set
        for seg in updated.segments:
            assert seg.duration_seconds == 7.5
            assert seg.audio_path is not None
            assert Path(seg.audio_path).exists()
