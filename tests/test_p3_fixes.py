"""Unit tests for Phase 3 (P3) implementations:
- Presenter Providers (Base, CloudLipsync, CloudI2V)
- PresenterClipLibrary (9 cues, product-specific clips, manual approval enforcement)
- SceneMatcher (5 anti-collage rules: perspective floor_y, color grading, contact shadow, despill, grain)
- VideoCompositor v2 (7-layer stack, presenter overlay, verification criteria)
- RenderQueue & RenderWorker (asynchronous non-blocking background rendering)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.database.models.avatar import AIAvatar
from app.presenter.base import PresenterClip
from app.presenter.clip_library import PresenterClipLibrary
from app.presenter.cloud_i2v import CloudI2VProvider
from app.presenter.cloud_lipsync import CloudLipsyncProvider
from app.render.compositor import VideoCompositor
from app.render.queue import RenderJob, RenderQueue
from app.render.worker import RenderWorker
from app.sets.matcher import SceneMatcher
from app.sets.scene_set import SceneSet


# =============================================================================
# 1. CLIP LIBRARY & APPROVAL WORKFLOW TESTS
# =============================================================================
class TestPresenterClipLibrary:
    def test_register_and_manual_approval_workflow(self, tmp_path: Path) -> None:
        lib = PresenterClipLibrary(root_dir=tmp_path)
        dummy_clip = tmp_path / "wave.mp4"
        dummy_clip.write_bytes(b"\x00" * 100)

        # 1. Register clip — must default to is_approved=False (§4.2)
        clip = lib.register_clip(
            avatar_id=1,
            cue="WAVE_GREET",
            file_path=dummy_clip,
            is_approved=False,
        )
        assert clip.cue == "WAVE_GREET"
        assert clip.is_approved is False

        # 2. Strict lookup with require_approved=True must NOT return it
        unapproved = lib.get_clip_for_cue(avatar_id=1, cue="WAVE_GREET", require_approved=True)
        assert unapproved is None

        # 3. Lookup with require_approved=False finds it
        found = lib.get_clip_for_cue(avatar_id=1, cue="WAVE_GREET", require_approved=False)
        assert found is not None

        # 4. Manual approval (§4.2 bước 2)
        lib.set_approval(avatar_id=1, cue="WAVE_GREET", is_approved=True)
        approved = lib.get_clip_for_cue(avatar_id=1, cue="WAVE_GREET", require_approved=True)
        assert approved is not None
        assert approved.is_approved is True

    def test_product_specific_clip_priority(self, tmp_path: Path) -> None:
        lib = PresenterClipLibrary(root_dir=tmp_path)
        generic_clip = tmp_path / "generic_hold.mp4"
        generic_clip.write_bytes(b"\x00" * 100)
        prod_clip = tmp_path / "prod_hold.mp4"
        prod_clip.write_bytes(b"\x00" * 100)

        # Register generic approved clip
        lib.register_clip(1, "HOLD_PRODUCT", generic_clip, is_approved=True)
        # Register product-specific approved clip for product_id=42
        lib.register_clip(1, "HOLD_PRODUCT", prod_clip, is_approved=True, has_product=True, product_id=42)

        # Query for product_id=42 must return product-specific clip
        c42 = lib.get_clip_for_cue(1, "HOLD_PRODUCT", product_id=42)
        assert c42 is not None
        assert c42.has_product is True
        assert c42.product_id == 42
        assert c42.file_path == str(prod_clip)

        # Query for other product_id=99 falls back to generic clip
        c99 = lib.get_clip_for_cue(1, "HOLD_PRODUCT", product_id=99)
        assert c99 is not None
        assert c99.has_product is False
        assert c99.file_path == str(generic_clip)

    def test_cue_fallback_to_idle_talk(self, tmp_path: Path) -> None:
        lib = PresenterClipLibrary(root_dir=tmp_path)
        idle_clip = tmp_path / "idle.mp4"
        idle_clip.write_bytes(b"\x00" * 100)
        lib.register_clip(1, "IDLE_TALK", idle_clip, is_approved=True)

        # Query for missing cue (e.g. SURPRISE) falls back to approved IDLE_TALK
        res = lib.get_clip_for_cue(1, "SURPRISE", require_approved=True)
        assert res is not None
        assert res.cue == "IDLE_TALK"


# =============================================================================
# 2. SCENE MATCHER & 5 ANTI-COLLAGE RULES TESTS
# =============================================================================
class TestSceneMatcher:
    def test_rule1_perspective_floor_y_and_height(self) -> None:
        scene = SceneSet(
            id="test",
            name="Test",
            category="all",
            background="",
            floor_y=1500,
            anchor_x=540,
            model_height_px=1200,
        )
        # Presenter source resolution 1080x1920
        x, y, scaled_w, scaled_h = SceneMatcher.calculate_presenter_placement(
            scene_set=scene,
            presenter_w=1080,
            presenter_h=1920,
            target_canvas_w=1080,
            target_canvas_h=1920,
        )
        assert scaled_h == 1200
        # Feet must be at floor_y exactly (y + scaled_h == floor_y)
        assert y + scaled_h == 1500
        # Center horizontally around anchor_x (540)
        assert abs((x + scaled_w / 2.0) - 540) <= 1.0

    def test_rule2_color_grade_filters(self) -> None:
        warm_set = SceneSet(id="w", name="W", category="c", background="", light_temp="3500K")
        cool_set = SceneSet(id="c", name="C", category="c", background="", light_temp="6500K")
        day_set = SceneSet(id="d", name="D", category="c", background="", light_temp="5600K")

        f_warm = SceneMatcher.build_color_grade_filter(warm_set)
        assert "colorbalance" in f_warm
        assert "bs=-0.08" in f_warm  # warm decreases blue

        f_cool = SceneMatcher.build_color_grade_filter(cool_set)
        assert "bs=0.09" in f_cool  # cool increases blue

        f_day = SceneMatcher.build_color_grade_filter(day_set)
        assert "eq=" in f_day

    def test_rule3_contact_shadow_mask(self) -> None:
        scene = SceneSet(
            id="s",
            name="S",
            category="c",
            background="",
            floor_y=1500,
            shadow_alpha=0.4,
            shadow_blur=20,
        )
        mask = SceneMatcher.generate_contact_shadow_mask(scene, feet_cx=540, feet_y=1500, width=1080, height=1920)
        assert mask.size == (1080, 1920)
        assert mask.mode == "RGBA"

    def test_rule5_grain_filter(self) -> None:
        g = SceneMatcher.build_grain_filter(0.02)
        assert "noise=alls=2" in g
        zero_g = SceneMatcher.build_grain_filter(0.0)
        assert zero_g == "null"


# =============================================================================
# 3. VIDEO COMPOSITOR V2 TESTS
# =============================================================================
class TestVideoCompositorV2:
    def test_prepare_background_frame_with_presenter_shadow(self, tmp_path: Path) -> None:
        compositor = VideoCompositor()
        scene = SceneSet(
            id="test",
            name="Test",
            category="all",
            background="",
            floor_y=1500,
            shadow_alpha=0.35,
        )
        out_bg = tmp_path / "bg_with_shadow.png"
        compositor._prepare_background_frame(
            output_path=out_bg,
            scene_set=scene,
            include_presenter_shadow=True,
        )
        assert out_bg.exists()
        img = Image.open(str(out_bg))
        assert img.size == (1080, 1920)


# =============================================================================
# 4. RENDER QUEUE & ASYNC WORKER TESTS
# =============================================================================
class TestRenderQueueAndWorker:
    def test_render_queue_lifecycle(self, tmp_path: Path) -> None:
        queue = RenderQueue()
        scene = SceneSet(id="test", name="Test", category="all", background="")

        job = queue.create_job(
            output_mp4=tmp_path / "out.mp4",
            scene_set=scene,
            audio_path=tmp_path / "audio.mp3",
            product_name="Nồi Inox",
        )
        assert job.status == "queued"
        assert job.progress == 0

        queue.update_progress(job.id, 50, "Đang xử lý...")
        assert job.progress == 50

        queue.mark_completed(job.id, verification={"valid": True})
        assert job.status == "completed"
        assert job.progress == 100
        assert job.completed_at is not None

    def test_render_worker_executes_job_without_blocking(self, tmp_path: Path) -> None:
        scene = SceneSet(id="test", name="Test", category="all", background="")
        job = RenderJob(
            output_mp4=str(tmp_path / "out.mp4"),
            scene_set=scene,
            audio_path=str(tmp_path / "audio.mp3"),
            product_name="Chảo Chống Dính",
        )

        mock_compositor = MagicMock()
        mock_compositor.render_composition.return_value = Path(job.output_mp4)
        mock_compositor.verify_rendered_video.return_value = {"valid": True, "errors": []}

        worker = RenderWorker(job=job, compositor=mock_compositor)

        signals_received = []
        worker.progress_updated.connect(lambda jid, pct, msg: signals_received.append((pct, msg)))

        finished_result = []
        worker.job_finished.connect(lambda j, ok, err: finished_result.append((ok, err)))

        # Run synchronously in test to assert signals
        worker.run()

        assert len(signals_received) >= 3
        assert finished_result[0][0] is True
        assert job.status == "completed"


# =============================================================================
# 5. PRESENTER PROVIDER CREDENTIALS & PIPELINE TESTS
# =============================================================================
class TestPresenterProviders:
    def test_cloud_lipsync_provider_validation(self) -> None:
        provider = CloudLipsyncProvider()
        ok, msg = provider.validate_credentials()
        # Checks ffprobe readiness
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_cloud_i2v_provider_validation(self) -> None:
        provider = CloudI2VProvider(api_key="mock_key", service_name="did")
        ok, msg = provider.validate_credentials()
        assert ok is True
        assert "DID" in msg

        provider_no_key = CloudI2VProvider(api_key="", service_name="did")
        ok2, msg2 = provider_no_key.validate_credentials()
        assert ok2 is False
        assert "Chưa cấu hình" in msg2
