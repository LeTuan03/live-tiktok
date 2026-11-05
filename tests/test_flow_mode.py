"""Unit tests cho chế độ Google Flow: gói việc, sổ tín dụng, canh thư mục tải về."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.integrations.flow.browser_automation import (
    DEFAULT_SELECTORS,
    js_set_prompt,
    library_js,
    load_selectors,
    parse_result,
)
from app.integrations.flow.constants import (
    AVATAR_CUES,
    FLOW_MODELS,
    build_avatar_prompt,
    credits_for,
)
from app.integrations.flow.credits import CreditLedger
from app.integrations.flow.pack import (
    KIND_AVATAR_BASE,
    KIND_AVATAR_CUE,
    KIND_PORTRAIT,
    KIND_PRODUCT_SCENE,
    build_avatar_pack,
    build_product_pack,
    list_packs,
    load_pack,
    slugify,
)
from app.integrations.flow.watcher import FlowDownloadWatcher


class TestCredits:
    def test_bang_gia_theo_model(self) -> None:
        assert credits_for("veo-3.1-fast") == 20
        assert credits_for("veo-3.1-quality") == 100
        # Model lạ thì rơi về giá mặc định, không nổ
        assert credits_for("khong-ton-tai") == FLOW_MODELS["veo-3.1-fast"].credits

    def test_tu_nhap_gia_ghi_de_bang_gia(self) -> None:
        assert credits_for("veo-3.1-fast", override=35) == 35

    def test_so_tin_dung_tru_dan_va_hoan_lai(self, tmp_path: Path) -> None:
        led = CreditLedger(path=tmp_path / "c.json", default_total=1050)
        assert led.remaining == 1050
        led.spend("clip 1", 20, job_id="j1")
        led.spend("clip 2", 20, job_id="j2")
        assert led.spent == 40
        assert led.remaining == 1010
        led.undo("j1")
        assert led.remaining == 1030

    def test_dong_bo_lai_theo_so_du_that(self, tmp_path: Path) -> None:
        led = CreditLedger(path=tmp_path / "c.json", default_total=1050)
        led.spend("x", 200)
        led.sync_to(700)
        assert led.remaining == 700
        assert led.spent == 0
        # Đọc lại từ đĩa phải giữ nguyên
        assert CreditLedger(path=tmp_path / "c.json").remaining == 700

    def test_can_afford(self, tmp_path: Path) -> None:
        led = CreditLedger(path=tmp_path / "c.json", default_total=50)
        assert led.can_afford(40)
        assert not led.can_afford(60)


class TestAvatarPack:
    def test_du_10_viec_va_khong_tao_trung_clip_nen(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Mai Phương", root=tmp_path)
        kinds = [j.kind for j in pack.jobs]
        assert kinds.count(KIND_PORTRAIT) == 1
        assert kinds.count(KIND_AVATAR_BASE) == 1
        # 9 cue nhưng IDLE_TALK đã là clip nền -> chỉ còn 8 clip cử chỉ
        assert kinds.count(KIND_AVATAR_CUE) == len(AVATAR_CUES) - 1
        assert len(pack.jobs) == 10

    def test_du_toan_tin_dung(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Mai Phương", model="veo-3.1-fast", root=tmp_path)
        # 9 video × 20 + 1 ảnh × 10
        assert pack.credits_estimate == 9 * 20 + 10

    def test_ten_file_bo_dau_tieng_viet(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Mai Phương", root=tmp_path)
        base = next(j for j in pack.jobs if j.kind == KIND_AVATAR_BASE)
        assert base.target_filename == "mai_phuong.mp4"
        cue = next(j for j in pack.jobs if j.cue == "WAVE_GREET")
        assert cue.target_filename == "mai_phuong_wave_greet.mp4"

    def test_prompt_ep_khung_doc_va_nen_xanh(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Test", root=tmp_path)
        cue = next(j for j in pack.jobs if j.cue == "WAVE_GREET")
        assert "9:16" in cue.prompt
        assert "#00FF00" in cue.prompt
        assert "no on-screen text" in cue.prompt

    def test_bo_tich_cu_chi_thi_chi_con_clip_nen(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Test", include_cues=[], include_portrait=False,
                                 root=tmp_path)
        assert len(pack.jobs) == 1
        assert pack.jobs[0].kind == KIND_AVATAR_BASE

    def test_ghi_du_file_ra_dia_va_nap_lai_duoc(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Mai Phương", root=tmp_path)
        assert (pack.dir / "manifest.json").exists()
        assert (pack.dir / "HUONG_DAN.txt").exists()
        prompts = list((pack.dir / "prompts").glob("*.txt"))
        assert len(prompts) == len(pack.jobs)

        again = load_pack(pack.dir)
        assert again.pack_id == pack.pack_id
        assert len(again.jobs) == len(pack.jobs)
        assert again.next_pending().job_id == pack.jobs[0].job_id

    def test_trang_thai_duoc_luu_lai(self, tmp_path: Path) -> None:
        pack = build_avatar_pack(name="Test", root=tmp_path)
        pack.jobs[0].status = "IMPORTED"
        pack.jobs[0].imported_path = "x.mp4"
        pack.save()
        again = load_pack(pack.dir)
        assert again.jobs[0].status == "IMPORTED"
        assert len(again.done_jobs) == 1
        assert again.credits_remaining_estimate == pack.credits_estimate - pack.jobs[0].credits

    def test_liet_ke_goi_moi_nhat_truoc(self, tmp_path: Path) -> None:
        first = build_avatar_pack(name="A", root=tmp_path)
        time.sleep(0.01)
        second = build_avatar_pack(name="B", root=tmp_path)
        packs = list_packs(tmp_path)
        assert [p.pack_id for p in packs][0] == second.pack_id
        assert first.pack_id in [p.pack_id for p in packs]


class TestProductPack:
    def _scenes(self) -> list[dict]:
        return [
            {"scene_index": 1, "shot_type": "HOOK", "veo_prompt": "Host holds the serum",
             "speech_text": "Chào cả nhà", "duration_seconds": 8},
            {"scene_index": 2, "shot_type": "CTA", "veo_prompt": "Host points down",
             "speech_text": "Chốt đơn ngay nhé", "duration_seconds": 8},
        ]

    def test_moi_canh_mot_viec(self, tmp_path: Path) -> None:
        pack = build_product_pack(title="Serum ABC", scenes=self._scenes(), root=tmp_path)
        assert len(pack.jobs) == 2
        assert all(j.kind == KIND_PRODUCT_SCENE for j in pack.jobs)
        assert pack.credits_estimate == 40

    def test_thoai_tieng_viet_vao_prompt_khi_bat_native(self, tmp_path: Path) -> None:
        pack = build_product_pack(title="X", scenes=self._scenes(), native_speech=True,
                                  root=tmp_path)
        assert "Chào cả nhà" in pack.jobs[0].prompt
        assert "DIALOGUE" in pack.jobs[0].prompt

    def test_clip_cam_khi_tat_native(self, tmp_path: Path) -> None:
        pack = build_product_pack(title="X", scenes=self._scenes(), native_speech=False,
                                  root=tmp_path)
        assert "Chào cả nhà" not in pack.jobs[0].prompt
        assert "no speech" in pack.jobs[0].prompt

    def test_ten_file_dich_khop_quy_uoc_cua_pipeline(self, tmp_path: Path) -> None:
        pack = build_product_pack(title="X", scenes=self._scenes(),
                                  project_id="veo_abc123", root=tmp_path)
        assert pack.jobs[0].target_filename == "veo_abc123_scene_1_raw.mp4"


class TestSlugify:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Mai Phương", "mai_phuong"),
            ("Thu  Thảo!", "thu_thao"),
            ("Đặng Hải Đăng", "dang_hai_dang"),
            ("", "nguoi_mau"),
        ],
    )
    def test_bo_dau_va_ky_tu_la(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected


class TestBrowserAutomation:
    def test_js_duoc_cai_config_vao(self) -> None:
        js = library_js()
        assert "__CONFIG__" not in js
        assert "setPrompt" in js and "clickGenerate" in js and "downloadSrc" in js

    def test_moi_lenh_js_deu_tra_json(self) -> None:
        # QtWebEngine không chuyển được object JS -> mọi lệnh phải JSON.stringify
        assert "JSON.stringify" in js_set_prompt("xin chào")
        assert "setPrompt" in js_set_prompt("xin chào")

    def test_prompt_duoc_escape_an_toan(self) -> None:
        js = js_set_prompt('dòng 1\n"trích dẫn"\\')
        assert "\n" not in js.split("setPrompt(")[1].split(")")[0]

    def test_parse_result(self) -> None:
        assert parse_result('{"ok": true}') == {"ok": True}
        assert parse_result('["a"]') == ["a"]
        assert parse_result("") is None
        assert parse_result("khong-phai-json") is None
        assert parse_result({"ok": False}) == {"ok": False}

    def test_bo_chon_ghi_de_tu_file(self, tmp_path: Path) -> None:
        path = tmp_path / "flow_selectors.json"
        path.write_text(json.dumps({"promptSelector": "textarea#p"}), encoding="utf-8")
        sel = load_selectors(path)
        assert sel["promptSelector"] == "textarea#p"
        assert sel["videoSelector"] == DEFAULT_SELECTORS["videoSelector"]

    def test_file_bo_chon_hong_khong_lam_chet_app(self, tmp_path: Path) -> None:
        path = tmp_path / "flow_selectors.json"
        path.write_text("{khong phai json", encoding="utf-8")
        assert load_selectors(path) == DEFAULT_SELECTORS


class TestPromptBuilder:
    def test_co_thoai_thi_them_dialogue(self) -> None:
        p = build_avatar_prompt("She waves.", appearance="A host", speech_vi="Xin chào")
        assert "DIALOGUE" in p and "Xin chào" in p

    def test_khong_thoai_thi_yeu_cau_cam(self) -> None:
        p = build_avatar_prompt("She waves.", appearance="A host")
        assert "does not speak" in p

    def test_nen_studio_khi_tat_nen_xanh(self) -> None:
        p = build_avatar_prompt("She waves.", appearance="A host", green_screen=False)
        assert "#00FF00" not in p
        assert "livestream studio" in p


class TestDownloadWatcher:
    def test_chi_bat_file_moi_va_da_ghi_xong(self, tmp_path: Path) -> None:
        old = tmp_path / "cu.mp4"
        old.write_bytes(b"x" * 100)
        w = FlowDownloadWatcher([tmp_path], settle_seconds=0)
        w.prime()
        assert w.poll() == []  # file cũ không tính

        new = tmp_path / "moi.mp4"
        new.write_bytes(b"y" * 100)
        assert w.poll() == []  # lần đầu chỉ ghi nhận kích thước
        found = w.poll()
        assert [p.name for p in found] == ["moi.mp4"]
        assert w.poll() == []  # không nhận lại lần hai

    def test_bo_qua_file_dang_tai_va_duoi_la(self, tmp_path: Path) -> None:
        (tmp_path / "dangtai.mp4.crdownload").write_bytes(b"x")
        (tmp_path / "ghichu.txt").write_bytes(b"x")
        w = FlowDownloadWatcher([tmp_path], settle_seconds=0)
        w.poll()
        assert w.poll() == []

    def test_forget_cho_phep_nhan_lai(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x" * 50)
        w = FlowDownloadWatcher([tmp_path], settle_seconds=0)
        w.poll()
        assert [p.name for p in w.poll()] == ["a.mp4"]
        w.forget(f)
        assert w.poll() == []  # ghi nhận lại kích thước trước
        assert [p.name for p in w.poll()] == ["a.mp4"]

    def test_mark_seen_chan_nhan_trung(self, tmp_path: Path) -> None:
        f = tmp_path / "b.mp4"
        f.write_bytes(b"x" * 50)
        w = FlowDownloadWatcher([tmp_path], settle_seconds=0)
        w.mark_seen(f)
        w.poll()
        assert w.poll() == []
