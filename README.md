# ⚡ LiveFlow AI Studio

**Ứng dụng desktop Windows hỗ trợ livestream bán hàng tự động bằng AI trên TikTok LIVE và Shopee Live.**

LiveFlow điều phối nội dung LIVE: lần lượt giới thiệu từng sản phẩm bằng video/ảnh + giọng đọc AI theo kịch
bản, hiển thị tên + giá, vòng lặp liên tục, trả lời bình luận bằng giọng nói — tất cả phát qua **OBS Studio**
tới TikTok/Shopee bằng RTMP chính thức.

```
Sản phẩm → Kịch bản AI → Giọng đọc TTS → Playlist → OBS (RTMP) → TikTok LIVE / Shopee Live
                                              ↑
                    Trợ lý bình luận (dán câu hỏi → AI gợi ý có căn cứ → duyệt → đọc trên LIVE)
```

## Tính năng
- 🎞️ **Google Flow**: nhúng flow.google.com vào app — tự dán prompt, tự bấm tạo, tự tải clip,
  tự chuẩn hoá 1080x1920 và nạp vào thư viện; đếm tín dụng Flow đã dùng. Không cần API key, không cần Billing.
- 📦 **Sản phẩm**: nhập tay, dán link Shopee/TikTok Shop (AI trích xuất), import/export CSV, gắn video/ảnh.
- 🎥 **Thư viện Media**: import video/ảnh/audio, đọc metadata (ffprobe), kiểm tra file thiếu, gắn với sản phẩm.
- 📝 **Script Studio**: AI viết kịch bản livestream theo loại/phong cách/thời lượng, chỉ dùng dữ liệu đã xác minh, lưu phiên bản.
- 🔊 **Voice Studio**: giọng đọc Microsoft Edge TTS (miễn phí), nghe thử, tạo audio hàng loạt, cache theo nội dung.
- 📅 **Playlist**: thứ tự sản phẩm, thời lượng, nghỉ giữa mục, lặp liên tục/số vòng.
- 🔴 **Live Studio**: kết nối OBS WebSocket, tạo scene/nguồn riêng, cấu hình RTMP TikTok/Shopee, Bắt đầu/Tạm dừng/Bỏ qua/Dừng điều phối, hàng đợi, nhật ký.
- 💬 **Trợ lý bình luận**: phân loại, gợi ý trả lời có căn cứ (dữ liệu sản phẩm + chính sách shop), duyệt, sao chép, đọc bằng giọng AI trên LIVE.
- ⚙️ **Cài đặt**: Gemini/OpenAI, OBS, thông tin shop, FFmpeg, nền tảng, xóa dữ liệu. API key lưu trong Windows Credential Manager.

## Yêu cầu
- Windows 10/11 64-bit · Python 3.12+ · OBS Studio 28+ (WebSocket 5 có sẵn) · Internet (AI + Edge TTS)
- FFmpeg (tùy chọn, để đọc thời lượng/độ phân giải video) — có script tải tự động.

## Cài đặt nhanh (máy mới)
1. Cài Python 3.12+ từ https://www.python.org/downloads/windows/ (tích **Add python.exe to PATH**).
2. Cài OBS Studio từ https://obsproject.com.
3. Giải nén/clone dự án, **nhấp đúp `INSTALL.bat`** (tạo venv, cài thư viện, hỏi tải FFmpeg).
4. **Nhấp đúp `RUN.bat`** để mở ứng dụng. `RUN_DEBUG.bat` mở kèm cửa sổ log khi cần tìm lỗi.

Dùng dòng lệnh:
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

## Đóng gói thành bản chạy không cần Python
```
BUILD_EXE.bat            → dist\LiveFlowAIStudio\LiveFlowAIStudio.exe (PyInstaller)
scripts\installer.iss    → mở bằng Inno Setup để tạo bộ cài Setup.exe
```

## Hướng dẫn sử dụng
Xem **[HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md)** (quy trình từng bước, cấu hình OBS dọc 9:16, TikTok/Shopee,
xử lý sự cố) và **[docs/TICH_HOP_TIKTOK_SHOPEE.md](docs/TICH_HOP_TIKTOK_SHOPEE.md)** (giới hạn tích hợp).

Có tín dụng Google Flow (gói Google AI Pro/Ultra)? Xem **[docs/HUONG_DAN_GOOGLE_FLOW.md](docs/HUONG_DAN_GOOGLE_FLOW.md)**
— chế độ mặc định: làm tất cả trong tab **🎞️ Google Flow** của app, không cần API key, không cần bật Billing.
Muốn gọi thẳng API Veo (phải bật Billing) thì xem **[docs/HUONG_DAN_GOOGLE_PRO.md](docs/HUONG_DAN_GOOGLE_PRO.md)**.

## Cấu trúc dự án
```
app/
├── main.py, bootstrap.py        # khởi động, kiểm tra dependency, excepthook
├── config/settings.py           # cấu hình (.env, LOCALAPPDATA)
├── core/                        # logging, events, exceptions, secrets (keyring)
├── database/                    # SQLAlchemy models, repositories, migrations có version
├── integrations/
│   ├── flow/                    # Google Flow: gói prompt, sổ tín dụng, điều khiển trình duyệt nhúng, nạp clip
│   ├── obs/client.py            # OBS WebSocket 5 (scene, media/image/text source, RTMP, stream)
│   ├── llm/                     # Gemini, OpenAI-compatible, factory
│   ├── tts/                     # Edge TTS
│   ├── ffmpeg/                  # ffprobe metadata
│   └── tiktok/capabilities.py   # ma trận capability TikTok/Shopee
├── services/                    # product, media, script, voice, comment, platform, scheduler (orchestrator), live_session, activity_log
└── ui/                          # PySide6: windows, pages, dialogs, styles
tests/                           # pytest
scripts/                         # download_ffmpeg.ps1, installer.iss, run_tests.bat
```

## Kiểm thử
```
scripts\run_tests.bat        (hoặc: pip install -r requirements-dev.txt && pytest -q)
```

## Giới hạn tích hợp TikTok/Shopee (tóm tắt)
| Tính năng | Trạng thái |
|---|---|
| Phát LIVE qua OBS (RTMP) | ✅ Hỗ trợ |
| Đọc bình luận | 🖐️ Thủ công — dán bình luận |
| Gửi trả lời | 🖐️ Thủ công — sao chép hoặc AI đọc bằng giọng nói |
| Ghim sản phẩm | 🖐️ Thủ công — có hướng dẫn |

Ứng dụng **không** scrape API riêng tư, không lưu mật khẩu nền tảng, không vượt CAPTCHA/OTP và không giả lập thành công.

## License
MIT
