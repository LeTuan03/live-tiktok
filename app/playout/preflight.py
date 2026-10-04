"""Preflight verification service — 12 strict pre-flight checks before going LIVE."""

from __future__ import annotations

import os
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from loguru import logger

from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.integrations.obs.client import OBSClient
from app.playout.obs_compositor import (
    CANVAS_FPS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_BANNER_SOURCE,
    DEFAULT_PLAYER_SOURCE,
    DEFAULT_SCENE,
    OBSCompositor,
)


@dataclass
class PreflightItem:
    """Individual pre-flight check item result."""

    id: str
    title: str
    status: str  # "PASS", "WARN", "FAIL"
    message: str
    remediation: str = ""


@dataclass
class PreflightReport:
    """Comprehensive pre-flight checklist report."""

    items: List[PreflightItem] = field(default_factory=list)

    @property
    def can_start_live(self) -> bool:
        """True if and only if NO check item has status 'FAIL'."""
        return all(item.status != "FAIL" for item in self.items)

    @property
    def pass_count(self) -> int:
        return sum(1 for i in self.items if i.status == "PASS")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.items if i.status == "WARN")

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.items if i.status == "FAIL")

    @property
    def summary(self) -> str:
        if self.can_start_live:
            if self.warn_count == 0:
                return f"✅ Tất cả {len(self.items)}/12 mục kiểm tra ĐẠT chuẩn. Sẵn sàng phát trực tiếp!"
            return f"⚠️ {self.pass_count}/12 mục ĐẠT, {self.warn_count} cảnh báo. Đủ điều kiện phát trực tiếp."
        return f"❌ {self.fail_count} mục KHÔNG ĐẠT (CHẶN CỨNG). Cần khắc phục trước khi bắt đầu LIVE."


class PreflightChecker:
    """Runs 12 hard blocking checklist items before livestreaming."""

    def __init__(self, obs_client: OBSClient) -> None:
        self.obs = obs_client
        self.ffprobe = FFProbeService()

    def run_full_preflight(
        self,
        scene_name: str = DEFAULT_SCENE,
        player_source: str = DEFAULT_PLAYER_SOURCE,
        banner_source: str = DEFAULT_BANNER_SOURCE,
        rtmp_server: str = "",
        stream_key: str = "",
        playlist_entries: Optional[List[Any]] = None,
        storage_path: Optional[str | Path] = None,
        min_free_disk_gb: float = 2.0,
    ) -> PreflightReport:
        """Execute all 12 preflight checks and return the structured report."""
        report = PreflightReport()

        # -------------------------------------------------------------
        # 1. OBS Connection
        # -------------------------------------------------------------
        if self.obs.is_connected:
            report.items.append(
                PreflightItem(
                    id="obs_connected",
                    title="Kết nối OBS Studio",
                    status="PASS",
                    message="OBS Studio đã kết nối thành công qua WebSocket.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="obs_connected",
                    title="Kết nối OBS Studio",
                    status="FAIL",
                    message=f"Chưa kết nối tới OBS Studio: {self.obs.last_error or 'Mất kết nối'}",
                    remediation="Bật OBS Studio, vào Tools → WebSocket Server Settings → bật WebSocket (cổng 4455), rồi bấm 'Kết nối OBS' trên LiveFlow.",
                )
            )

        # -------------------------------------------------------------
        # 2. OBS Version & WebSocket 5.x
        # -------------------------------------------------------------
        if self.obs.is_connected:
            status = self.obs.get_status()
            obs_ver = status.obs_version
            ws_ver = status.ws_version
            if obs_ver:
                report.items.append(
                    PreflightItem(
                        id="obs_version",
                        title="Phiên bản OBS Studio",
                        status="PASS",
                        message=f"OBS v{obs_ver} (WebSocket v{ws_ver or '5.x'}).",
                    )
                )
            else:
                report.items.append(
                    PreflightItem(
                        id="obs_version",
                        title="Phiên bản OBS Studio",
                        status="WARN",
                        message="Không đọc được số phiên bản OBS.",
                        remediation="Đảm bảo OBS Studio là phiên bản 28.0 trở lên.",
                    )
                )
        else:
            report.items.append(
                PreflightItem(
                    id="obs_version",
                    title="Phiên bản OBS Studio",
                    status="FAIL",
                    message="Không thể kiểm tra phiên bản OBS khi chưa kết nối.",
                    remediation="Kết nối OBS Studio trước.",
                )
            )

        # -------------------------------------------------------------
        # 3. Canvas Resolution (1080x1920)
        # -------------------------------------------------------------
        video_settings = self.obs.get_video_settings() if self.obs.is_connected else {}
        bw = video_settings.get("base_width", 0)
        bh = video_settings.get("base_height", 0)

        if bw == CANVAS_WIDTH and bh == CANVAS_HEIGHT:
            report.items.append(
                PreflightItem(
                    id="canvas_resolution",
                    title="Kích thước Canvas 9:16",
                    status="PASS",
                    message=f"Canvas chuẩn dọc: {bw}×{bh} (1080×1920).",
                )
            )
        elif self.obs.is_connected:
            report.items.append(
                PreflightItem(
                    id="canvas_resolution",
                    title="Kích thước Canvas 9:16",
                    status="FAIL",
                    message=f"Canvas hiện tại là {bw}×{bh}, không đúng chuẩn dọc 9:16 ({CANVAS_WIDTH}×{CANVAS_HEIGHT}).",
                    remediation="Bấm nút 'Chuẩn hoá OBS' hoặc vào Cài đặt OBS → Video → đặt Base Resolution thành 1080x1920.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="canvas_resolution",
                    title="Kích thước Canvas 9:16",
                    status="FAIL",
                    message="Không thể kiểm tra canvas khi chưa kết nối OBS.",
                    remediation="Kết nối OBS Studio và chuẩn hoá canvas.",
                )
            )

        # -------------------------------------------------------------
        # 4. Canvas Framerate (FPS)
        # -------------------------------------------------------------
        fps = video_settings.get("fps", 0)
        if fps >= 25:
            report.items.append(
                PreflightItem(
                    id="canvas_fps",
                    title="Tốc độ khung hình (FPS)",
                    status="PASS",
                    message=f"FPS hiện tại: {fps} fps.",
                )
            )
        elif self.obs.is_connected and fps > 0:
            report.items.append(
                PreflightItem(
                    id="canvas_fps",
                    title="Tốc độ khung hình (FPS)",
                    status="WARN",
                    message=f"FPS hiện tại ({fps}) thấp hơn khuyến nghị 30fps.",
                    remediation="Khuyên dùng 30fps cho livestream bán hàng trên TikTok/Shopee.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="canvas_fps",
                    title="Tốc độ khung hình (FPS)",
                    status="FAIL",
                    message="Không xác định được FPS của OBS.",
                    remediation="Kiểm tra cấu hình Video trong OBS.",
                )
            )

        # -------------------------------------------------------------
        # 5. Scene Readiness
        # -------------------------------------------------------------
        if self.obs.is_connected:
            scenes = self.obs.get_scenes()
            if scene_name in scenes:
                report.items.append(
                    PreflightItem(
                        id="obs_scene_ready",
                        title=f"Scene '{scene_name}'",
                        status="PASS",
                        message=f"Scene chuẩn '{scene_name}' đã sẵn sàng.",
                    )
                )
            else:
                report.items.append(
                    PreflightItem(
                        id="obs_scene_ready",
                        title=f"Scene '{scene_name}'",
                        status="FAIL",
                        message=f"Chưa tìm thấy scene '{scene_name}' trong OBS.",
                        remediation="Bấm 'Tạo cấu hình OBS' trên trang Live Studio để tạo scene tự động.",
                    )
                )
        else:
            report.items.append(
                PreflightItem(
                    id="obs_scene_ready",
                    title="Scene phát trực tiếp",
                    status="FAIL",
                    message="Chưa kết nối OBS để kiểm tra scene.",
                    remediation="Kết nối OBS trước.",
                )
            )

        # -------------------------------------------------------------
        # 6. Source Readiness (Player & Banner)
        # -------------------------------------------------------------
        if self.obs.is_connected and scene_name in (scenes or []):
            items = self.obs.get_scene_item_list(scene_name)
            item_names = {i["name"] for i in items if i.get("name")}
            if player_source in item_names:
                banner_note = "" if (not banner_source or banner_source in item_names) else " (chưa có lớp banner)"
                report.items.append(
                    PreflightItem(
                        id="obs_sources_ready",
                        title="Nguồn phát Video Player",
                        status="PASS",
                        message=f"Nguồn '{player_source}' đã có trong scene.{banner_note}",
                    )
                )
            else:
                report.items.append(
                    PreflightItem(
                        id="obs_sources_ready",
                        title="Nguồn phát Video Player",
                        status="FAIL",
                        message=f"Thiếu nguồn phát '{player_source}' trong scene '{scene_name}'.",
                        remediation="Bấm 'Tạo cấu hình OBS' để thêm media source phát video vào scene.",
                    )
                )
        else:
            report.items.append(
                PreflightItem(
                    id="obs_sources_ready",
                    title="Nguồn phát Video Player",
                    status="FAIL",
                    message="Không thể kiểm tra nguồn phát do scene chưa sẵn sàng hoặc mất kết nối OBS.",
                    remediation="Thiết lập scene và nguồn phát trong OBS.",
                )
            )

        # -------------------------------------------------------------
        # 7. Audio Mute Status (Bug #3 fix)
        # -------------------------------------------------------------
        if self.obs.is_connected and player_source:
            is_muted = self.obs.get_input_mute(player_source)
            if is_muted is True:
                report.items.append(
                    PreflightItem(
                        id="audio_not_muted",
                        title="Âm thanh nguồn phát",
                        status="FAIL",
                        message=f"Nguồn âm thanh '{player_source}' đang bị TẮT TIẾNG (Muted) trong OBS! Livestream sẽ bị câm.",
                        remediation="Mở OBS → Bộ trộn âm thanh (Audio Mixer) → bấm bỏ biểu tượng loa gạch chéo của nguồn để bật tiếng.",
                    )
                )
            elif is_muted is False:
                vol = self.obs.get_input_volume(player_source)
                mul = vol.get("mul", 1.0)
                if mul <= 0.001:
                    report.items.append(
                        PreflightItem(
                            id="audio_not_muted",
                            title="Âm thanh nguồn phát",
                            status="WARN",
                            message=f"Nguồn âm thanh '{player_source}' đang có volume = 0.",
                            remediation="Kéo thanh âm lượng của nguồn lên trong OBS Audio Mixer.",
                        )
                    )
                else:
                    report.items.append(
                        PreflightItem(
                            id="audio_not_muted",
                            title="Âm thanh nguồn phát",
                            status="PASS",
                            message=f"Nguồn âm thanh '{player_source}' đã bật tiếng (volume {int(mul * 100)}%).",
                        )
                    )
            else:
                report.items.append(
                    PreflightItem(
                        id="audio_not_muted",
                        title="Âm thanh nguồn phát",
                        status="WARN",
                        message=f"Không đọc được trạng thái âm thanh của '{player_source}'.",
                        remediation="Kiểm tra thủ công trong Audio Mixer của OBS.",
                    )
                )
        else:
            report.items.append(
                PreflightItem(
                    id="audio_not_muted",
                    title="Âm thanh nguồn phát",
                    status="FAIL",
                    message="Chưa kết nối OBS hoặc chưa có nguồn phát.",
                    remediation="Kết nối OBS và thêm nguồn phát.",
                )
            )

        # -------------------------------------------------------------
        # 8. Stream Configuration (RTMP & Key)
        # -------------------------------------------------------------
        rtmp = (rtmp_server or "").strip()
        key = (stream_key or "").strip()
        if not rtmp or not key:
            report.items.append(
                PreflightItem(
                    id="stream_configured",
                    title="Cấu hình RTMP & Stream Key",
                    status="FAIL",
                    message="Chưa nhập Server URL hoặc Stream Key.",
                    remediation="Mở trang 'Live Studio', điền Server URL và Stream Key từ TikTok LIVE Studio / Shopee LIVE rồi bấm 'Lưu & gửi vào OBS'.",
                )
            )
        elif not rtmp.lower().startswith(("rtmp://", "rtmps://")):
            report.items.append(
                PreflightItem(
                    id="stream_configured",
                    title="Cấu hình RTMP & Stream Key",
                    status="FAIL",
                    message=f"Server URL '{rtmp}' không hợp lệ (phải bắt đầu bằng rtmp:// hoặc rtmps://).",
                    remediation="Kiểm tra lại Server URL được cấp trong TikTok / Shopee.",
                )
            )
        elif len(key) < 6:
            report.items.append(
                PreflightItem(
                    id="stream_configured",
                    title="Cấu hình RTMP & Stream Key",
                    status="WARN",
                    message="Stream Key có độ dài bất thường (quá ngắn).",
                    remediation="Kiểm tra lại Stream Key bạn đã copy.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="stream_configured",
                    title="Cấu hình RTMP & Stream Key",
                    status="PASS",
                    message=f"Đã cấu hình endpoint {rtmp[:25]}... và Stream Key.",
                )
            )

        # -------------------------------------------------------------
        # 9. Free Disk Space
        # -------------------------------------------------------------
        target_dir = Path(storage_path or os.getcwd())
        try:
            usage = shutil.disk_usage(str(target_dir))
            free_gb = usage.free / (1024**3)
            if free_gb < 1.0:
                report.items.append(
                    PreflightItem(
                        id="disk_space",
                        title="Dung lượng ổ đĩa trống",
                        status="FAIL",
                        message=f"Ổ đĩa lưu trữ chỉ còn {free_gb:.2f} GB trống (yêu cầu tối thiểu {min_free_disk_gb} GB).",
                        remediation="Dọn dẹp ổ đĩa, giải phóng ít nhất 2GB dung lượng để lưu video render và bản ghi OBS.",
                    )
                )
            elif free_gb < min_free_disk_gb:
                report.items.append(
                    PreflightItem(
                        id="disk_space",
                        title="Dung lượng ổ đĩa trống",
                        status="WARN",
                        message=f"Ổ đĩa còn {free_gb:.1f} GB trống (khuyến nghị ≥ 5 GB).",
                        remediation="Nên chuẩn bị thêm dung lượng cho các phiên live dài.",
                    )
                )
            else:
                report.items.append(
                    PreflightItem(
                        id="disk_space",
                        title="Dung lượng ổ đĩa trống",
                        status="PASS",
                        message=f"Ổ đĩa còn {free_gb:.1f} GB trống.",
                    )
                )
        except Exception as e:
            report.items.append(
                PreflightItem(
                    id="disk_space",
                    title="Dung lượng ổ đĩa trống",
                    status="WARN",
                    message=f"Không kiểm tra được dung lượng ổ đĩa: {e}",
                )
            )

        # -------------------------------------------------------------
        # 10. FFmpeg / FFprobe availability
        # -------------------------------------------------------------
        if self.ffprobe.is_available():
            report.items.append(
                PreflightItem(
                    id="ffmpeg_available",
                    title="Công cụ FFmpeg & FFprobe",
                    status="PASS",
                    message="FFmpeg / FFprobe đã được cài đặt và sẵn sàng.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="ffmpeg_available",
                    title="Công cụ FFmpeg & FFprobe",
                    status="FAIL",
                    message="Không tìm thấy ffprobe / ffmpeg trên máy.",
                    remediation="Cài đặt FFmpeg hoặc cấu hình đường dẫn ffmpeg_path trong Cài đặt.",
                )
            )

        # -------------------------------------------------------------
        # 11. Product Prices Verified (Fix #7: chặn cứng giá ảo)
        # -------------------------------------------------------------
        if playlist_entries is None or len(playlist_entries) == 0:
            report.items.append(
                PreflightItem(
                    id="prices_verified",
                    title="Xác minh giá sản phẩm",
                    status="FAIL",
                    message="Playlist không có sản phẩm nào.",
                    remediation="Thêm ít nhất 1 sản phẩm vào playlist trước khi bắt đầu LIVE.",
                )
            )
        else:
            unverified_items: list[str] = []
            for entry in playlist_entries:
                p_name = getattr(entry, "product_name", None) or getattr(entry, "name", "Sản phẩm")
                price = getattr(entry, "price", None)
                promo = getattr(entry, "promo_price", None)

                # Strict check: Price must be present and positive
                if price is None or price <= 0:
                    unverified_items.append(f"{p_name} (chưa có giá gốc)")
                elif promo is not None and promo > price:
                    unverified_items.append(f"{p_name} (giá khuyến mãi lớn hơn giá gốc)")

            if unverified_items:
                preview = ", ".join(unverified_items[:3])
                if len(unverified_items) > 3:
                    preview += f" và {len(unverified_items) - 3} sản phẩm khác"
                report.items.append(
                    PreflightItem(
                        id="prices_verified",
                        title="Xác minh giá sản phẩm",
                        status="FAIL",
                        message=f"Có {len(unverified_items)} sản phẩm chưa có giá hợp lệ hoặc thông số sai lệch: {preview}.",
                        remediation="Chặn cứng theo quy tắc an toàn: Hãy vào trang Sản phẩm, điền và xác minh giá bán thực tế trước khi lên sóng.",
                    )
                )
            else:
                report.items.append(
                    PreflightItem(
                        id="prices_verified",
                        title="Xác minh giá sản phẩm",
                        status="PASS",
                        message=f"Tất cả {len(playlist_entries)} sản phẩm đều có giá hợp lệ đã được xác minh.",
                    )
                )

        # -------------------------------------------------------------
        # 12. Network Connectivity
        # -------------------------------------------------------------
        net_ok = self._check_network_connectivity()
        if net_ok:
            report.items.append(
                PreflightItem(
                    id="network_ready",
                    title="Kết nối Internet",
                    status="PASS",
                    message="Kết nối mạng internet ổn định.",
                )
            )
        else:
            report.items.append(
                PreflightItem(
                    id="network_ready",
                    title="Kết nối Internet",
                    status="FAIL",
                    message="Không có kết nối Internet hoặc mạng bị gián đoạn.",
                    remediation="Kiểm tra lại kết nối mạng Wifi/Ethernet trước khi phát sóng.",
                )
            )

        return report

    def _check_network_connectivity(self, timeout: float = 3.0) -> bool:
        """Probe external DNS / IP to verify internet connectivity."""
        for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                sock.close()
                return True
            except Exception:
                continue
        return False
