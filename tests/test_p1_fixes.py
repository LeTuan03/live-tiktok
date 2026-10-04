"""Unit and integration tests for P1 features — OBS Compositor, Banner generator, Preflight, and Filler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.playout.filler import create_filler_clip, generate_standby_image
from app.playout.obs_compositor import (
    CANVAS_FPS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    OBSCompositor,
)
from app.playout.preflight import PreflightChecker, PreflightItem, PreflightReport
from app.render.banner import BannerGenerator, format_vnd, wrap_text


# =====================================================================
# 1. Tests for Banner Generator (render/banner.py)
# =====================================================================
class TestBannerGenerator:
    def test_format_vnd(self):
        assert format_vnd(199000) == "199.000₫"
        assert format_vnd(1500000) == "1.500.000₫"
        assert format_vnd(0) == "Liên hệ"
        assert format_vnd(None) == "Liên hệ"

    def test_banner_dimensions_and_mode(self, tmp_path):
        bg = BannerGenerator()
        img = bg.render_overlay(
            product_name="Nồi Cơm Điện Cao Tần Sunhouse 1.8L",
            price=2500000,
            promo_price=1790000,
            shop_name="Sunhouse Official",
            is_live=True,
            ai_generated_label=True,
        )
        assert isinstance(img, Image.Image)
        assert img.size == (1080, 1920)
        assert img.mode == "RGBA"

        # Check save_overlay
        out_file = tmp_path / "test_banner.png"
        saved = bg.save_overlay(
            product_name="Nồi Cơm Điện Cao Tần Sunhouse 1.8L",
            output_path=out_file,
            price=2500000,
            promo_price=1790000,
        )
        assert saved.exists()
        assert saved.stat().st_size > 0

    def test_banner_without_promo_price(self):
        bg = BannerGenerator()
        img = bg.render_overlay(
            product_name="Máy Xay Sinh Tố Cầm Tay",
            price=450000,
            promo_price=None,
        )
        assert img.size == (1080, 1920)

    def test_banner_with_long_title_wraps(self):
        bg = BannerGenerator()
        long_title = (
            "Combo 3 Hộp Viên Giặt Quần Áo Đậm Đặc Kháng Khuẩn Khử Mùi "
            "Công Nghệ Sinh Học Nhật Bản Mùi Hương Hoa Anh Đào Cao Cấp 100 Viên"
        )
        img = bg.render_overlay(
            product_name=long_title,
            price=399000,
            promo_price=199000,
        )
        assert img.size == (1080, 1920)


# =====================================================================
# 2. Tests for OBS Compositor (playout/obs_compositor.py)
# =====================================================================
class TestOBSCompositor:
    def test_enforce_canvas_format(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = True
        mock_obs.get_video_settings.return_value = {
            "base_width": 1920,
            "base_height": 1080,
            "fps": 60,
        }
        mock_obs.set_video_settings.return_value = (True, "Canvas updated")

        compositor = OBSCompositor(mock_obs)
        ok, msg = compositor.enforce_canvas_format(1080, 1920, 30)

        assert ok is True
        mock_obs.set_video_settings.assert_called_once_with(
            base_width=1080,
            base_height=1920,
            out_width=1080,
            out_height=1920,
            fps_num=30,
            fps_den=1,
        )

    def test_enforce_z_order(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = True
        # Initial scene items
        mock_obs.get_scene_item_list.side_effect = [
            # Call 1: initial items
            [
                {"id": 101, "name": "LiveFlow Banner", "index": 0},
                {"id": 102, "name": "LiveFlow Player", "index": 1},
            ],
            # Call 2: verified items after setting
            [
                {"id": 102, "name": "LiveFlow Player", "index": 0},
                {"id": 101, "name": "LiveFlow Banner", "index": 1},
            ],
        ]
        mock_obs.get_scene_item_id.side_effect = lambda sc, nm: 102 if nm == "LiveFlow Player" else 101

        compositor = OBSCompositor(mock_obs)
        layers = ["LiveFlow Player", "LiveFlow Banner"]
        ok, msg = compositor.enforce_z_order("LiveFlow Live", layers)

        assert ok is True
        assert "Z-order chuẩn xác" in msg
        mock_obs.set_scene_item_index.assert_any_call("LiveFlow Live", 102, 0)
        mock_obs.set_scene_item_index.assert_any_call("LiveFlow Live", 101, 1)

    def test_verify_composition_detects_errors(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = True
        # Canvas mismatch + muted audio
        mock_obs.get_video_settings.return_value = {"base_width": 1920, "base_height": 1080, "fps": 30}
        mock_obs.get_scenes.return_value = ["LiveFlow Live"]
        mock_obs.get_scene_item_list.return_value = [
            {"id": 1, "name": "LiveFlow Player", "index": 1},
            {"id": 2, "name": "LiveFlow Banner", "index": 0},  # Banner under player
        ]
        mock_obs.get_input_mute.return_value = True  # Muted!
        mock_obs.get_input_volume.return_value = {"mul": 1.0, "db": 0.0}

        compositor = OBSCompositor(mock_obs)
        res = compositor.verify_composition()

        assert res["valid"] is False
        assert any("Kích thước canvas" in e for e in res["errors"])
        assert any("TẮT TIẾNG" in e for e in res["errors"])
        assert any("Z-order chưa chuẩn" in w for w in res["warnings"])


# =====================================================================
# 3. Tests for Preflight 12-Check Service (playout/preflight.py)
# =====================================================================
class TestPreflightChecker:
    @dataclass
    class DummyProduct:
        product_name: str
        price: float
        promo_price: float

    def test_preflight_blocks_when_obs_offline(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = False
        mock_obs.last_error = "Connection refused"

        checker = PreflightChecker(mock_obs)
        report = checker.run_full_preflight()

        assert report.can_start_live is False
        assert report.fail_count > 0
        obs_item = next(i for i in report.items if i.id == "obs_connected")
        assert obs_item.status == "FAIL"

    def test_preflight_blocks_unverified_prices(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = True
        mock_obs.get_status.return_value = MagicMock(obs_version="30.1.0", ws_version="5.3.0")
        mock_obs.get_video_settings.return_value = {"base_width": 1080, "base_height": 1920, "fps": 30}
        mock_obs.get_scenes.return_value = ["LiveFlow Live"]
        mock_obs.get_scene_item_list.return_value = [{"name": "LiveFlow Player", "index": 0}]
        mock_obs.get_input_mute.return_value = False
        mock_obs.get_input_volume.return_value = {"mul": 1.0, "db": 0.0}

        # Item 1 has no price! (Bug #7: must block!)
        bad_entries = [
            {"product_name": "Sản phẩm A", "price": None, "promo_price": None},
        ]

        checker = PreflightChecker(mock_obs)
        with patch.object(checker, "_check_network_connectivity", return_value=True):
            report = checker.run_full_preflight(
                rtmp_server="rtmp://live.tiktok.com/app",
                stream_key="secret-key-123",
                playlist_entries=[MagicMock(product_name="SP Test", price=None, promo_price=None)],
            )

        assert report.can_start_live is False
        price_check = next(i for i in report.items if i.id == "prices_verified")
        assert price_check.status == "FAIL"
        assert "chưa có giá gốc" in price_check.message

    def test_preflight_all_pass_when_configured_properly(self):
        mock_obs = MagicMock()
        mock_obs.is_connected = True
        mock_obs.get_status.return_value = MagicMock(obs_version="30.2.0", ws_version="5.3.0")
        mock_obs.get_video_settings.return_value = {"base_width": 1080, "base_height": 1920, "fps": 30}
        mock_obs.get_scenes.return_value = ["LiveFlow Live"]
        mock_obs.get_scene_item_list.return_value = [
            {"name": "LiveFlow Player", "index": 0},
            {"name": "LiveFlow Banner", "index": 1},
        ]
        mock_obs.get_input_mute.return_value = False
        mock_obs.get_input_volume.return_value = {"mul": 1.0, "db": 0.0}

        valid_entries = [
            MagicMock(product_name="Nồi chiên", price=1200000, promo_price=890000),
            MagicMock(product_name="Bàn là hơi", price=450000, promo_price=390000),
        ]

        checker = PreflightChecker(mock_obs)
        with patch.object(checker, "_check_network_connectivity", return_value=True):
            report = checker.run_full_preflight(
                rtmp_server="rtmp://live.tiktok.com/app",
                stream_key="valid-tiktok-stream-key-xyz",
                playlist_entries=valid_entries,
                min_free_disk_gb=1.0,
            )

        assert report.can_start_live is True
        assert report.fail_count == 0
        assert len(report.items) == 12


# =====================================================================
# 4. Tests for Filler Clip Generation (playout/filler.py)
# =====================================================================
class TestFillerClip:
    def test_generate_standby_image(self, tmp_path):
        target_img = tmp_path / "standby.png"
        path = generate_standby_image(target_img, title="Đang đổi sản phẩm")
        assert path.exists()
        with Image.open(str(path)) as img:
            assert img.size == (1080, 1920)

    def test_create_filler_clip(self, tmp_path):
        target_mp4 = tmp_path / "test_filler.mp4"
        out_path = create_filler_clip(output_mp4=target_mp4, duration_seconds=3)
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_scheduler_plays_filler_on_consecutive_errors_without_stopping(self):
        from app.services.scheduler_service import LiveOrchestrator, OrchestratorConfig, PlaylistEntry, RunState
        entries = [
            PlaylistEntry(item_id=1, product_id=1, product_name="X1", video_path=r"C:\nope\missing1.mp4", duration_seconds=0.05),
            PlaylistEntry(item_id=2, product_id=2, product_name="X2", video_path=r"C:\nope\missing2.mp4", duration_seconds=0.05),
            PlaylistEntry(item_id=3, product_id=3, product_name="X3", video_path=r"C:\nope\missing3.mp4", duration_seconds=0.05),
        ]
        cfg = OrchestratorConfig(
            playlist_id=None,
            playlist_name="test_filler",
            entries=entries,
            loop=False,
            max_consecutive_errors=2,
            use_filler_fallback=True,
        )
        orchestrator = LiveOrchestrator(cfg, obs_client=None)
        with patch.object(orchestrator, "_play_filler") as mock_filler:
            orchestrator.run()
            # Assert filler was played when consecutive errors hit 2
            assert mock_filler.called
