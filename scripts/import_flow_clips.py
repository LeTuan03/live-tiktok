"""Nạp video người mẫu tải từ Google Flow vào LiveFlow — bản dòng lệnh (dự phòng).

Cách dùng chính bây giờ là **ngay trong app**: tab "🎞️ Google Flow" tự dán prompt,
tự bấm tạo, tự tải và tự nạp. Script này dành cho khi bạn đã có sẵn một thư mục video
(tải bằng Chrome, lấy từ máy khác, hay làm lại hàng loạt).

Dùng:
    # 1. Chỉ kiểm tra, chưa đụng vào gì
    python scripts/import_flow_clips.py "C:\\Users\\Ban\\Downloads\\flow"

    # 2. Chuẩn hoá và nạp vào LiveFlow
    python scripts/import_flow_clips.py "C:\\Users\\Ban\\Downloads\\flow" --cai-dat

    # 3. Nạp clip cử chỉ cho người mẫu số 1, video chính đặt tên mai_phuong
    python scripts/import_flow_clips.py "...\\flow" --cai-dat --avatar-id 1 --ten mai_phuong

Cách script đoán clip nào là gì: dựa vào TÊN FILE. Đặt tên có chứa từ khoá của cue
(wave, hold, detail, price, tryon, cta, nod, surprise, idle) thì script tự xếp đúng chỗ.
File không khớp từ khoá nào sẽ được coi là video người mẫu chính.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.integrations.flow.importer import (  # noqa: E402
    FFmpegMissingError,
    VIDEO_SUFFIXES,
    find_tools,
    inspect_clip,
    make_poster,
    normalize,
)

# Từ khoá trong tên file -> cue trong LiveFlow
CUE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "WAVE_GREET": ("wave", "greet", "chao", "vaytay"),
    "HOLD_PRODUCT": ("hold", "camsp", "camsanpham", "holdproduct"),
    "SHOW_DETAIL": ("detail", "chitiet", "showdetail"),
    "POINT_PRICE": ("price", "gia", "pointprice"),
    "TRY_ON": ("tryon", "macthu", "thu"),
    "CTA_POINT_DOWN": ("cta", "giohang", "pointdown"),
    "NOD_LISTEN": ("nod", "gatdau", "lang nghe", "langnghe"),
    "SURPRISE": ("surprise", "ngacnhien", "bat ngo", "batngo"),
    "IDLE_TALK": ("idle", "talk", "thuyetminh", "noi"),
}


def guess_cue(filename: str) -> Optional[str]:
    low = filename.lower().replace("-", "").replace("_", "").replace(" ", "")
    for cue, keys in CUE_KEYWORDS.items():
        for k in keys:
            if k.replace(" ", "") in low:
                return cue
    return None


def avatar_output_dir() -> Path:
    try:
        from app.config.settings import get_settings

        return get_settings().effective_avatar_dir
    except Exception:
        d = PROJECT_ROOT / "storage" / "avatars"
        d.mkdir(parents=True, exist_ok=True)
        return d


def main() -> int:
    ap = argparse.ArgumentParser(description="Nap video nguoi mau tu Flow vao LiveFlow")
    ap.add_argument("thu_muc", help="Thu muc chua file .mp4 tai tu Flow")
    ap.add_argument("--cai-dat", action="store_true",
                    help="Chuan hoa va nap vao LiveFlow (khong co co nay thi chi kiem tra)")
    ap.add_argument("--avatar-id", type=int, default=None,
                    help="ID nguoi mau de gan clip cu chi (xem trong tab Nguoi Mau)")
    ap.add_argument("--ten", default="mai_phuong",
                    help="Ten file cho video chinh (mac dinh: mai_phuong)")
    ap.add_argument("--video-chinh", default=None,
                    help="Chi dinh ro file nao la video nguoi mau chinh (vd: model_idle_loop.mp4). "
                         "Dung khi ten file trung tu khoa cue nen bi xep nham.")
    args = ap.parse_args()

    src_dir = Path(args.thu_muc)
    if not src_dir.is_dir():
        print(f"[LOI] Khong thay thu muc: {src_dir}")
        return 1

    try:
        ffmpeg, ffprobe = find_tools()
    except FFmpegMissingError as e:
        print(f"[LOI] {e}")
        return 1

    videos = sorted(
        p for p in src_dir.iterdir()
        if p.suffix.lower() in VIDEO_SUFFIXES and p.is_file()
    )
    if not videos:
        print(f"[!] Khong co file video nao trong {src_dir}")
        return 1

    print(f"\nTim thay {len(videos)} video trong {src_dir}\n")
    print(f"{'FILE':<34} {'PHAN LOAI':<16} {'KHUNG':<11} {'DAI':>6} {'NEN XANH':>9}  TIENG")
    print("-" * 92)

    chinh = (args.video_chinh or "").strip().lower()
    if chinh and not any(v.name.lower() == chinh for v in videos):
        print(f"[!] --video-chinh '{args.video_chinh}' khong co trong thu muc, bo qua co nay.")
        chinh = ""

    records: List[dict] = []
    for v in videos:
        cue = guess_cue(v.name)
        if chinh and v.name.lower() == chinh:
            cue = None  # nguoi dung chi dinh ro day la video chinh
        insp = inspect_clip(
            v,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            expect_green=True,
            expect_silent=bool(cue),
            max_duration=12 if cue else 0,
        )
        records.append({"path": v, "cue": cue, "insp": insp})

        loai = cue or "VIDEO CHINH"
        dai = f"{insp.duration:.1f}s" if insp.ok else "-"
        gr = f"{insp.green_ratio:.0%}" if insp.green_ratio is not None else "-"
        tieng = "co" if insp.has_audio else "khong"
        print(f"{v.name[:33]:<34} {loai:<16} {insp.frame_label:<11} {dai:>6} {gr:>9}  {tieng}")

    canh_bao = [(r["path"].name, w) for r in records for w in r["insp"].warnings]
    if canh_bao:
        print("\n--- CANH BAO ---")
        for name, w in canh_bao:
            print(f"  [{name[:30]}] {w}")

    if not args.cai_dat:
        print("\nDay moi la kiem tra. Them --cai-dat de chuan hoa va nap vao LiveFlow.")
        return 0

    # ---------------- Cai dat ----------------
    print("\n--- DANG CHUAN HOA VA NAP ---")
    out_dir = avatar_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    so_chinh = so_cue = 0

    library = None
    if args.avatar_id is not None:
        try:
            from app.presenter.clip_library import PresenterClipLibrary

            library = PresenterClipLibrary()
        except Exception as e:
            print(f"[!] Khong mo duoc thu vien clip ({e}) — se chi copy file, khong dang ky.")

    for rec in records:
        insp = rec["insp"]
        if not insp.ok:
            print(f"  [BO QUA] {rec['path'].name}: file loi")
            continue

        src, cue = rec["path"], rec["cue"]
        dest = out_dir / (f"{args.ten}_{cue.lower()}.mp4" if cue else f"{args.ten}.mp4")

        print(f"  Dang xu ly {src.name} -> {dest.name} ...", flush=True)
        if not normalize(src, dest, ffmpeg=ffmpeg, pad_color="0x00FF00", keep_audio=not cue):
            print(f"  [LOI] Khong chuan hoa duoc {src.name}")
            continue

        if cue:
            so_cue += 1
            if library and args.avatar_id is not None:
                try:
                    library.register_clip(
                        avatar_id=args.avatar_id,
                        cue=cue,
                        file_path=str(dest),
                        duration=insp.duration,
                        is_approved=False,  # buoc nguoi van hanh xem lai roi moi duyet
                        notes="Tao bang Google Flow",
                    )
                    print(f"    da dang ky cue {cue} (CHUA duyet)")
                except Exception as e:
                    print(f"    [!] Khong dang ky duoc cue: {e}")
        else:
            so_chinh += 1
            poster = dest.with_suffix(".jpg")
            if make_poster(dest, poster, ffmpeg=ffmpeg):
                print(f"    da tao anh dai dien {poster.name}")

    print(f"\n[XONG] {so_chinh} video chinh, {so_cue} clip cu chi -> {out_dir}")
    print("\nBuoc tiep theo trong LiveFlow:")
    print("  1. Mo tab 'Quan Ly Nguoi Mau AI' -> nguoi mau da dung video moi")
    if so_cue:
        if args.avatar_id is None:
            print("  2. Bam '9 Cues' va nap tung clip (chay lai voi --avatar-id de tu dang ky)")
        else:
            print("  2. Bam '9 Cues' -> xem lai tung clip -> bam DUYET")
    print("  3. Bam 'OBS' de kiem tra tach nen xanh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
