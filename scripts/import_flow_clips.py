"""Kiểm tra và nạp video người mẫu tải từ Google Flow / Gemini vào LiveFlow.

Bạn bấm tạo video trong Flow rồi tải về một thư mục. Script này lo phần còn lại:
kiểm tra khung hình, chất lượng nền xanh, chuẩn hoá về 1080x1920, đặt đúng tên,
copy vào đúng chỗ và đăng ký clip cử chỉ vào thư viện.

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
import json
import shutil
import subprocess
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

AVATAR_DIR = PROJECT_ROOT / "storage" / "avatars"

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

TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30


# ---------------------------------------------------------------------------
# Tìm ffmpeg / ffprobe
# ---------------------------------------------------------------------------
def find_tools() -> Tuple[str, str]:
    try:
        from app.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe

        ffmpeg = find_ffmpeg() or shutil.which("ffmpeg")
        ffprobe = find_ffprobe() or shutil.which("ffprobe")
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")

    if not ffmpeg or not ffprobe:
        print("[LOI] Khong tim thay ffmpeg/ffprobe.")
        print("      Chay INSTALL.bat va chon tai FFmpeg, hoac dat LIVEFLOW_FFMPEG_PATH trong .env")
        sys.exit(1)
    return str(ffmpeg), str(ffprobe)


def probe(ffprobe: str, path: Path) -> Optional[dict]:
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Đo chất lượng nền xanh
# ---------------------------------------------------------------------------
def measure_green(ffmpeg: str, path: Path, tmp_dir: Path) -> Optional[float]:
    """Trả về tỉ lệ pixel nền xanh ở khung giữa clip (0.0 - 1.0)."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    tmp_dir.mkdir(parents=True, exist_ok=True)
    frame = tmp_dir / f"_probe_{path.stem}.png"
    cmd = [ffmpeg, "-v", "error", "-ss", "1", "-i", str(path),
           "-frames:v", "1", "-y", str(frame)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
        if not frame.exists():
            return None
        arr = np.array(Image.open(frame).convert("RGB")).astype(np.int16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        green = (g > 90) & (g - r > 40) & (g - b > 40)
        return float(green.mean())
    except Exception:
        return None
    finally:
        frame.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phân loại & kiểm tra
# ---------------------------------------------------------------------------
def guess_cue(filename: str) -> Optional[str]:
    low = filename.lower().replace("-", "").replace("_", "").replace(" ", "")
    for cue, keys in CUE_KEYWORDS.items():
        for k in keys:
            if k.replace(" ", "") in low:
                return cue
    return None


def inspect(ffmpeg: str, ffprobe: str, path: Path, tmp_dir: Path) -> dict:
    info = probe(ffprobe, path)
    rec: dict = {"path": path, "cue": guess_cue(path.name), "ok": True, "canh_bao": []}

    if not info:
        rec["ok"] = False
        rec["canh_bao"].append("Khong doc duoc file (hong hoac khong phai video)")
        return rec

    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vstreams:
        rec["ok"] = False
        rec["canh_bao"].append("Khong co luong video")
        return rec

    v = vstreams[0]
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rec["w"], rec["h"] = w, h
    rec["co_tieng"] = bool(astreams)
    try:
        rec["thoi_luong"] = float(info.get("format", {}).get("duration", 0))
    except Exception:
        rec["thoi_luong"] = 0.0

    # Khung dọc 9:16
    if h <= w:
        rec["canh_bao"].append(f"Khung NGANG {w}x{h} — can khung DOC 9:16, se phai them vien den")
    elif abs((w / h) - (9 / 16)) > 0.02:
        rec["canh_bao"].append(f"Ti le {w}x{h} khong chuan 9:16 — se bi them vien")

    # Nền xanh
    green = measure_green(ffmpeg, path, tmp_dir)
    rec["nen_xanh"] = green
    if green is None:
        rec["canh_bao"].append("Khong do duoc nen xanh (thieu Pillow/numpy)")
    elif green < 0.12:
        rec["canh_bao"].append(
            f"Nen xanh chi {green:.0%} — qua it, chroma key se khong tach duoc nguoi mau"
        )
    elif green < 0.30:
        rec["canh_bao"].append(f"Nen xanh {green:.0%} — hoi it, kiem tra lai khi len OBS")

    # Độ dài
    if rec["cue"] and rec["thoi_luong"] > 12:
        rec["canh_bao"].append(f"Clip cu chi dai {rec['thoi_luong']:.1f}s — nen cat con 3-5s")
    if not rec["cue"] and 0 < rec["thoi_luong"] < 5:
        rec["canh_bao"].append(f"Video chinh chi {rec['thoi_luong']:.1f}s — loop se lap lai rat nhanh")

    return rec


# ---------------------------------------------------------------------------
# Chuẩn hoá
# ---------------------------------------------------------------------------
def normalize(ffmpeg: str, src: Path, dest: Path) -> bool:
    """Scale/pad ve 1080x1920, h264 yuv420p — dinh dang OBS doc muot nhat."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
        f"fps={TARGET_FPS}"
    )
    cmd = [ffmpeg, "-v", "error", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-y", str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        return r.returncode == 0 and dest.exists() and dest.stat().st_size > 1000
    except Exception:
        return False


def make_poster(ffmpeg: str, video: Path, dest_jpg: Path) -> bool:
    cmd = [ffmpeg, "-v", "error", "-ss", "1", "-i", str(video),
           "-frames:v", "1", "-q:v", "2", "-y", str(dest_jpg)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
        return dest_jpg.exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
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

    ffmpeg, ffprobe = find_tools()
    tmp_dir = src_dir / "_liveflow_tmp"

    videos = sorted(
        p for p in src_dir.iterdir()
        if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv") and p.is_file()
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
        rec = inspect(ffmpeg, ffprobe, v, tmp_dir)
        if chinh and v.name.lower() == chinh:
            rec["cue"] = None  # nguoi dung chi dinh ro day la video chinh
        records.append(rec)
        loai = rec["cue"] or "VIDEO CHINH"
        khung = f"{rec.get('w','?')}x{rec.get('h','?')}" if rec["ok"] else "-"
        dai = f"{rec.get('thoi_luong',0):.1f}s" if rec["ok"] else "-"
        gr = rec.get("nen_xanh")
        gr_s = f"{gr:.0%}" if gr is not None else "-"
        tieng = "co" if rec.get("co_tieng") else "khong"
        print(f"{v.name[:33]:<34} {loai:<16} {khung:<11} {dai:>6} {gr_s:>9}  {tieng}")

    canh_bao = [(r["path"].name, w) for r in records for w in r["canh_bao"]]
    if canh_bao:
        print("\n--- CANH BAO ---")
        for name, w in canh_bao:
            print(f"  [{name[:30]}] {w}")

    if not args.cai_dat:
        print("\nDay moi la kiem tra. Them --cai-dat de chuan hoa va nap vao LiveFlow.")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return 0

    # ---------------- Cài đặt ----------------
    print("\n--- DANG CHUAN HOA VA NAP ---")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    so_chinh = so_cue = 0

    library = None
    if args.avatar_id is not None:
        try:
            from app.presenter.clip_library import PresenterClipLibrary

            library = PresenterClipLibrary()
        except Exception as e:
            print(f"[!] Khong mo duoc thu vien clip ({e}) — se chi copy file, khong dang ky.")

    for rec in records:
        if not rec["ok"]:
            print(f"  [BO QUA] {rec['path'].name}: file loi")
            continue

        src = rec["path"]
        if rec["cue"]:
            dest = AVATAR_DIR / f"{args.ten}_{rec['cue'].lower()}.mp4"
        else:
            dest = AVATAR_DIR / f"{args.ten}.mp4"

        print(f"  Dang xu ly {src.name} -> {dest.name} ...", flush=True)
        if not normalize(ffmpeg, src, dest):
            print(f"  [LOI] Khong chuan hoa duoc {src.name}")
            continue

        if rec["cue"]:
            so_cue += 1
            if library and args.avatar_id is not None:
                try:
                    library.register_clip(
                        avatar_id=args.avatar_id,
                        cue=rec["cue"],
                        file_path=str(dest),
                        duration=rec.get("thoi_luong", 0.0),
                        is_approved=False,  # buoc nguoi van hanh xem lai roi moi duyet
                    )
                    print(f"    da dang ky cue {rec['cue']} (CHUA duyet)")
                except Exception as e:
                    print(f"    [!] Khong dang ky duoc cue: {e}")
        else:
            so_chinh += 1
            poster = AVATAR_DIR / f"{args.ten}.jpg"
            if make_poster(ffmpeg, dest, poster):
                print(f"    da tao anh dai dien {poster.name}")

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n[XONG] {so_chinh} video chinh, {so_cue} clip cu chi -> {AVATAR_DIR}")
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
