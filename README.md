# ⚡ LiveFlow AI Studio

**Công cụ AI hỗ trợ livestream bán hàng trên TikTok**

LiveFlow AI Studio là ứng dụng desktop Windows giúp người bán tự động hóa quy trình livestream bán hàng trên TikTok, bao gồm quản lý sản phẩm, tạo kịch bản AI, tạo giọng nói TTS, điều phối video qua OBS Studio và hỗ trợ trả lời bình luận.

---

## ✨ Tính năng chính

- 📦 **Quản lý sản phẩm** — Thêm, sửa, xóa, import/export CSV
- 🎥 **Thư viện Media** — Import video, xem metadata, gắn với sản phẩm
- 📝 **AI Script Studio** — Tạo kịch bản giới thiệu sản phẩm bằng AI
- 🔊 **AI Voice Studio** — Chuyển kịch bản thành giọng nói tự nhiên (TTS)
- 🔴 **Live Studio** — Điều phối livestream qua OBS Studio
- 📅 **Lịch trình** — Tự động trình chiếu sản phẩm theo thứ tự
- 💬 **Trợ lý bình luận** — AI đề xuất câu trả lời cho bình luận
- ⚙️ **Cài đặt linh hoạt** — Hỗ trợ nhiều AI/TTS provider

---

## 🔧 Yêu cầu hệ thống

- Windows 10/11 64-bit
- Python 3.12+
- OBS Studio (có WebSocket plugin)
- FFmpeg (khuyến nghị)

---

## 🚀 Cài đặt

### 1. Clone repository

```bash
git clone <repository-url>
cd live-tiktok
```

### 2. Tạo virtual environment (khuyến nghị)

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Cài đặt dependencies

```bash
pip install -r requirements.txt
```

### 4. Cấu hình

```bash
copy .env.example .env
# Chỉnh sửa .env theo nhu cầu
```

### 5. Chạy ứng dụng

```bash
python -m app.main
```

---

## 📁 Cấu trúc dự án

```
live-tiktok/
├── app/
│   ├── main.py              # Entry point
│   ├── bootstrap.py          # Dependency checking
│   ├── config/               # Settings (Pydantic)
│   ├── core/                 # Events, logging, exceptions, task manager
│   ├── database/             # SQLAlchemy models, session, migrations
│   ├── services/             # Business logic
│   ├── integrations/         # OBS, FFmpeg, LLM, TTS, TikTok
│   ├── workers/              # Background workers
│   ├── ui/                   # PySide6 UI
│   │   ├── windows/          # Main window
│   │   ├── pages/            # Navigation pages
│   │   ├── dialogs/          # Modal dialogs
│   │   ├── components/       # Reusable widgets
│   │   └── styles/           # Dark theme
│   └── utils/                # Utility functions
├── assets/                   # Static assets
├── tests/                    # pytest tests
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 🔒 Bảo mật

- API keys được lưu trong Windows Credential Manager
- Không hardcode secret trong source code
- Dữ liệu sản phẩm chỉ gửi tới AI khi đã cấu hình provider
- Log file không chứa API key hoặc password

---

## 📋 Giới hạn tích hợp TikTok

LiveFlow AI Studio sử dụng capability-based architecture cho TikTok:

| Tính năng | Trạng thái |
|-----------|-----------|
| Đọc bình luận | Manual Mode — người dùng nhập/dán bình luận |
| Gửi bình luận | Manual Mode — AI đề xuất, người dùng tự gửi |
| Ghim sản phẩm | Manual Mode — hiển thị hướng dẫn ghim thủ công |

Ứng dụng **không** scrape private API, reverse-engineer, hoặc tự động né cơ chế bảo mật.

---

## 📄 License

MIT
