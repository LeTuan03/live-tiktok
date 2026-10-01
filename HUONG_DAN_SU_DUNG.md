# 📖 HƯỚNG DẪN SỬ DỤNG VÀ VẬN HÀNH LIVEFLOW AI STUDIO

**LiveFlow AI Studio** là ứng dụng Desktop cao cấp hỗ trợ tự động hóa livestream bán hàng trên TikTok/Facebook Live. Tích hợp trí tuệ nhân tạo (AI Script Generation), tổng hợp giọng nói (Edge TTS), và điều phối luồng phát qua OBS Studio.

---

## 🛠️ 1. YÊU CẦU HỆ THỐNG

| Thành phần | Yêu cầu tối thiểu | Khuyến nghị |
| :--- | :--- | :--- |
| **Hệ điều hành** | Windows 10 (64-bit) | Windows 11 (64-bit) |
| **Python** | Python 3.12+ | Python 3.14 (64-bit) |
| **RAM** | 8 GB | 16 GB |
| **Phần mềm kèm theo**| OBS Studio 28+ (WebSocket v5) | OBS Studio 30+ |

---

## 🚀 2. HƯỚNG DẪN CÀI ĐẶT

### Bước 1: Mở môi trường Terminal / PowerShell
Mở **PowerShell** hoặc **Command Prompt** tại thư mục dự án `live-tiktok`:

```powershell
cd d:\frontend\live-tiktok
```

### Bước 2: Tạo và kích hoạt Môi trường ảo (Virtual Environment)
```powershell
python -m venv venv
.\venv\Scripts\activate
```

### Bước 3: Cài đặt các thư viện cần thiết
```powershell
pip install -r requirements.txt
```

---

## ⚙️ 3. CẤU HÌNH BAN ĐẦU

### 1. Cấu hình File `.env`
Tạo file `.env` từ file mẫu `.env.example`:

```powershell
copy .env.example .env
```

Nội dung file `.env` mẫu:
```env
# AI Provider (OpenAI hoặc Custom Endpoint như Ollama/vLLM)
OPENAI_API_KEY=sk-your-openai-api-key-here
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o-mini

# OBS WebSocket 5.x
OBS_HOST=localhost
OBS_PORT=4455
OBS_PASSWORD=
```

### 2. Cấu hình OBS Studio (Nếu dùng với OBS)
1. Mở **OBS Studio**.
2. Trên thanh menu, chọn `Công cụ (Tools)` -> `Cấu hình WebSocket Server (WebSocket Server Settings)`.
3. Tích chọn **Bật WebSocket Server (Enable WebSocket Server)**.
4. Đảm bảo Cổng (Port) là `4455`. (Nếu cài Password, hãy nhập lại password này vào ứng dụng LiveFlow AI).

---

## 🎮 4. HƯỚNG DẪN KHỞI CHẠY ỨNG DỤNG

### Chạy ứng dụng giao diện (Desktop App)
```powershell
python -m app.main
```

### Kiểm tra tự động (Run Unit Tests)
```powershell
python -m unittest discover -s tests -p "test_*.py"
```

---

## 🖥️ 5. HƯỚNG DẪN CHI TIẾT CÁC CHỨC NĂNG

### 📦 1. Quản lý Sản phẩm (Product Page)
- **Thêm sản phẩm mới**: Bấm `➕ Thêm sản phẩm`, điền thông tin *Tên sản phẩm, Giá bán, Mã SKU, Mô tả, USP tính năng*.
- **Nhập/Xuất CSV**: 
  - Bấm `📥 Import CSV` để tải lên danh sách hàng loạt.
  - Bấm `📤 Export CSV` để trích xuất danh sách sản phẩm.
- **Tìm kiếm**: Nhập tên hoặc mã SKU vào ô tìm kiếm để lọc nhanh.

### 📝 2. AI Script Studio (Script Page)
- **Tạo kịch bản AI**:
  1. Chọn sản phẩm từ danh sách.
  2. Tùy chỉnh *Phong cách* (Chuyên gia tư vấn, Thân thiện, Năng lượng, Ngắn gọn), *Xưng hô* (Anh/Chị, Cả nhà), *Lời kêu gọi chốt đơn (CTA)*.
  3. Bấm `✨ Tạo kịch bản bằng AI`.
- **Chỉnh sửa & Quản lý phiên bản**:
  - Chỉnh sửa nội dung kịch bản trực tiếp trên màn hình editor.
  - Bấm `💾 Lưu kịch bản` để lưu phiên bản mới.
  - Lịch sử phiên bản (*v1, v2, v3...*) hiển thị bên cột trái để chuyển đổi bất cứ lúc nào.

### 🔊 3. AI Voice Studio (Voice Page)
- **Quản lý Giọng đọc (TTS Profiles)**:
  - Hỗ trợ các giọng đọc chuẩn Tiếng Việt từ Microsoft Edge TTS (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`).
  - Thêm profile mới, tùy chỉnh tốc độ nói (*0.8x, 1.0x, 1.2x*), đặt giọng mặc định.
- **Đọc thử & Xuất file Audio**:
  - Nhập văn bản mẫu và bấm `▶️ Đọc thử TTS` để nghe kiểm tra ngữ điệu.
  - Trong trang Script Studio, bấm `🔊 Tạo âm thanh TTS` để tổng hợp file âm thanh `.mp3` cho kịch bản.

### 🔴 4. Live Studio (Live Studio Page)
- **Kết nối OBS Studio**: Nhập Host/Port/Password và bấm `🔌 Kết nối OBS WebSocket`.
- **Đổi Cảnh (Scene Switcher)**: Chọn cảnh (*Scene*) từ danh sách thả xuống để chuyển đổi trực tiếp trên OBS.
- **Điều phối Stream**:
  - Bấm `▶️ Bắt Đầu Stream` để kích hoạt đếm giờ và bắt đầu điều phối.
  - Bấm `⏹️ Dừng Stream` khi kết thúc phiên live.

### 📅 5. Lịch Trình & Playlist (Scheduler Page)
- **Tạo Playlist**: Đặt tên danh sách trình chiếu sản phẩm tự động.
- **Xoay vòng sản phẩm**: Đặt chế độ *Lặp lại liên tục (Loop)* và *Thời gian nghỉ giữa các sản phẩm (Giây)*.
- **Thêm sản phẩm vào Queue**: Chọn sản phẩm từ kho để đưa vào chuỗi phát tự động.

### 💬 6. Trợ Lý Bình Luận (Comment Page)
- **Tự động nhận diện từ khóa**: Cấu hình các quy tắc trả lời tự động cho câu hỏi về *Giá cả, Phí vận chuyển, Hàng chính hãng*.
- **Giả lập & Phê duyệt trả lời**: Giả lập bình luận của người xem và xem câu trả lời đề xuất từ AI.

### ⚙️ 7. Cài Đặt (Settings Page)
- Cấu hình API Key cho OpenAI / Ollama.
- Cấu hình OBS WebSocket Host/Port.
- Xem bảng ma trận khả năng tích hợp TikTok Live API (Capabilities Matrix).

---

## ❓ 6. GIẢI QUYẾT SỰ CỐ THƯỜNG GẶP (TROUBLESHOOTING)

### 1. Không mở được ứng dụng khi chạy `python -m app.main`
- **Nguyên nhân**: Chưa cài đặt thư viện hoặc chưa activate môi trường venv.
- **Khắc phục**: Chạy `.\venv\Scripts\activate` và `pip install -r requirements.txt`.

### 2. Báo lỗi `Không thể kết nối tới OBS Studio`
- **Nguyên nhân**: OBS Studio chưa bật WebSocket Server hoặc nhập sai Port/Password.
- **Khắc phục**: Mở OBS -> `Tools` -> `WebSocket Server Settings` -> Bật Server và kiểm tra lại cổng 4455.

### 3. Không có âm thanh khi phát TTS
- **Nguyên nhân**: Thiết bị chưa kết nối Internet để tải giọng từ Edge TTS server.
- **Khắc phục**: Kiểm tra kết nối mạng hoặc chuyển sang giọng đọc local.

---

*LiveFlow AI Studio — Giải pháp livestream tự động cho nhà bán hàng TikTok.*
