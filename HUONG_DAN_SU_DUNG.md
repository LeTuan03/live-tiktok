# 📖 HƯỚNG DẪN SỬ DỤNG LIVEFLOW AI STUDIO (v1.0.0)

LiveFlow AI Studio giúp bạn **LIVE bán hàng tự động** trên TikTok LIVE (và Shopee Live): mỗi sản phẩm được
giới thiệu bằng video hoặc ảnh banner + giọng đọc AI theo kịch bản, hiển thị tên + giá, chạy vòng lặp liên tục;
bạn chỉ cần dán câu hỏi của khách để AI gợi ý trả lời (có thể đọc luôn bằng giọng nói trên LIVE).

---

## 1. Yêu cầu

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10/11 64-bit |
| Python | 3.12 trở lên (chỉ cần khi chạy từ mã nguồn; bản đóng gói EXE không cần) |
| OBS Studio | 28 trở lên (đã có sẵn WebSocket 5) — https://obsproject.com |
| Internet | Cần cho AI (Gemini/OpenAI) và giọng đọc Edge TTS |
| FFmpeg | Tùy chọn (đọc thời lượng/độ phân giải video). Script tải tự động có sẵn |
| Tài khoản | TikTok/Shopee đủ điều kiện LIVE bằng OBS (Stream Key). Gemini API Key miễn phí |

---

## 2. Cài đặt (máy mới)

1. Cài **Python 3.12+** (tích *Add python.exe to PATH* khi cài) và **OBS Studio**.
2. Chép thư mục dự án vào máy (ví dụ `D:\LiveFlow`).
3. Nhấp đúp **`INSTALL.bat`** → chờ cài thư viện (2–5 phút). Khi được hỏi, chọn **Y** để tải FFmpeg tự động.
4. Nhấp đúp **`RUN.bat`** để mở ứng dụng. (`RUN_DEBUG.bat` hiển thị log nếu cần tìm lỗi.)

Dữ liệu (database, audio, log) nằm ở `%LOCALAPPDATA%\LiveFlowAIStudio` — mở nhanh bằng nút *Mở thư mục dữ liệu*
trong Cài đặt. Video gốc của bạn **không** bị sao chép hay xóa.

---

## 3. Thiết lập lần đầu (10 phút)

### 3.1 AI Provider
Cài đặt → **AI Provider** → chọn *Google Gemini* → dán API Key (lấy miễn phí tại
https://aistudio.google.com/app/apikey, bấm nút *Copy* để lấy đủ key) → **Kiểm tra kết nối AI** → **Lưu cấu hình**.
Nếu báo lỗi model 404: bấm **Tải danh sách model** rồi chọn model khác.

> Dùng OpenAI/Ollama: chọn *OpenAI / tương thích*, nhập API Key (hoặc Base URL `http://localhost:11434/v1` cho Ollama) và tên model.

### 3.2 OBS Studio
1. Mở OBS → **Tools (Công cụ)** → **WebSocket Server Settings** → tích *Enable WebSocket server* (port 4455).
   Nếu đặt mật khẩu, nhập cùng mật khẩu vào LiveFlow.
2. **Canvas dọc cho TikTok/Shopee**: OBS → Settings → Video → *Base (Canvas) Resolution* và *Output* đặt **1080×1920**
   (hoặc 720×1280), FPS 30.
3. Trong LiveFlow → **Live Studio** → nhập Host/Port/Password → **Kết nối OBS** → bấm
   **🛠️ Tạo scene & nguồn 'LiveFlow' trong OBS**. LiveFlow tạo scene `LiveFlow AI` với 4 nguồn:
   `LiveFlow Image` (ảnh sản phẩm), `LiveFlow Video`, `LiveFlow Audio`, `LiveFlow Text` (tên + giá).
   Các scene/nguồn khác của bạn không bị đụng tới.
4. Trang trí như LIVE mẫu (banner tím, "FREESHIP", voucher, ảnh người mẫu…): trong OBS, thêm **Image source nền của bạn**
   vào scene `LiveFlow AI` và kéo xuống **dưới cùng**; có thể thêm logo, khung giá, chữ chạy tùy ý. Kéo/thu nhỏ
   `LiveFlow Image`/`LiveFlow Video` vào vùng bạn muốn hiển thị sản phẩm.

### 3.3 Thông tin shop (để AI trả lời khách đúng chính sách)
Cài đặt → **Thông tin shop**: nhập chính sách vận chuyển, đổi trả, bảo hành đúng thực tế. Bỏ trống mục nào thì AI
sẽ trả lời "em kiểm tra lại" thay vì tự bịa.

---

## 4. Quy trình chuẩn bị một buổi LIVE

Bảng điều khiển có **checklist 6 bước** với nút *Đi tới*; làm theo thứ tự:

### Bước 1 — Sản phẩm
- **➕ Thêm sản phẩm**: nhập tên, giá, giá KM, tính năng đã xác minh, phân loại/size… Nút **✨ AI Tự Điền** tạo
  *bản nháp* — hãy đối chiếu thông tin thật rồi mới lưu.
- **🔗 Thêm từ Link**: dán link Shopee/TikTok Shop/Tiki/Lazada, AI trích xuất thông số để bạn kiểm tra.
- **📥 Import CSV** để nhập hàng loạt (xem cột mẫu khi **📤 Export CSV**).
- Nút **🎬** trên mỗi dòng: gắn video/ảnh cho sản phẩm. Ô **Ảnh sản phẩm** trong form cũng dùng được.

### Bước 2 — Media
- **Thư viện Media** → **📥 Import file** / **📁 Import thư mục**: video (.mp4…), ảnh (.jpg/.png), audio.
- Chọn file → **🔗 Gắn với sản phẩm**. Có video thì OBS phát video; chỉ có ảnh thì hiển thị ảnh (kiểu banner + giọng AI
  như LIVE mẫu).
- **🩺 Kiểm tra file thiếu** trước khi LIVE.

### Bước 3 — Kịch bản + giọng đọc
- **Script Studio** → tab *Kho sản phẩm* → chọn sản phẩm → **✨ AI Tự Động Sinh Kịch Bản** (hoặc tùy chỉnh loại
  kịch bản, phong cách, xưng hô, deal, CTA, thời lượng rồi **🎯 Tạo kịch bản theo cấu hình**).
- Sửa lại nội dung nếu cần → **💾 Lưu kịch bản** → **🔊 Tạo âm thanh TTS**.
- **Voice Studio**: chọn giọng (Hoài My/Nam Minh…), tốc độ, **▶️ Nghe thử**; nút
  **🚀 Tạo audio cho tất cả kịch bản còn thiếu** để làm hàng loạt.
- Mẹo: tạo thêm "sản phẩm" tên *Chào mừng* / *Nhắc voucher* với kịch bản riêng để chèn vào playlist như lời dẫn.

### Bước 4 — Playlist
- **Playlist & Lịch trình** → tạo playlist → **⚡ Thêm nhanh tất cả sản phẩm đang bật** hoặc **➕ Thêm sản phẩm**
  (chọn video/ảnh, kịch bản, audio, thời lượng — để 0 = theo audio).
- Sắp xếp **⬆️⬇️**, bật/tắt từng mục, đặt *Nghỉ giữa sản phẩm* và *Lặp liên tục*. Cột trạng thái cho biết mục nào
  còn thiếu kịch bản/audio.

### Bước 5 — Lên LIVE
1. Mở OBS, mở phòng LIVE trên **TikTok LIVE Studio** (hoặc app Shopee) và lấy **Server URL + Stream Key**
   (xem *❓ Lấy Stream Key ở đâu?* trong Live Studio).
2. **Live Studio** → *Kết nối OBS* → chọn nền tảng → dán Server URL + Stream Key → **📤 Gửi cấu hình RTMP vào OBS**.
3. Chọn playlist, chọn **Âm thanh TTS phát qua**:
   - *Media source trong OBS* (khuyên dùng khi LIVE: âm thanh đi thẳng vào stream, không phụ thuộc loa máy).
   - *Máy tính này*: phát ra loa, OBS thu qua *Desktop Audio*.
4. **🔴 Bắt đầu LIVE** (OBS Start Streaming) → **▶️ Bắt đầu điều phối**. Theo dõi *Sản phẩm hiện tại*, thanh
   tiến độ, hàng đợi và nhật ký. Dùng **⏸️ Tạm dừng / ⏭️ Bỏ qua / ⏹️ Dừng** bất cứ lúc nào.
5. Kết thúc: **⏹️ Dừng** điều phối → **⏹️ Kết thúc LIVE**. LiveFlow không bao giờ tự tắt OBS.

### Bước 6 — Trả lời bình luận trong lúc LIVE
- **Trợ lý bình luận** → chọn sản phẩm đang nói → dán câu hỏi của khách (mỗi dòng một câu, có thể dạng
  `tên: nội dung`) → **⚡ Phân loại & gợi ý trả lời**.
- AI phân loại (giá, tính năng, ship, size, bảo hành, spam…) và gợi ý câu trả lời **chỉ dựa trên dữ liệu sản phẩm
  + chính sách shop**. Mục có ⚠️ nghĩa là thiếu dữ liệu — AI chỉ hứa kiểm tra lại.
- Sửa câu trả lời nếu muốn → **✅ Duyệt** → **📋 Sao chép** dán lên TikTok/Shopee → **📨 Đã gửi thủ công**,
  hoặc **🔊 Đọc trên LIVE** để giọng AI đọc câu trả lời xen vào ngay sau câu đang nói.
- Auto-reply bị khóa vì TikTok/Shopee chưa cấp API gửi bình luận hợp lệ (xem docs/TICH_HOP_TIKTOK_SHOPEE.md).

---

## 5. Cài đặt khác
- **Khi bấm X**: hỏi *Thoát* hay *Thu nhỏ xuống khay*; tích "Ghi nhớ" hoặc đổi trong Cài đặt.
- **FFmpeg**: chọn thư mục chứa `ffprobe.exe` nếu không tự nhận.
- **Xóa toàn bộ dữ liệu người dùng**: Cài đặt → nút đỏ (xóa DB, audio, key; không xóa video gốc).
- Log: `%LOCALAPPDATA%\LiveFlowAIStudio\logs` (file `errors_*.log` chỉ chứa lỗi).

---

## 6. Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Không mở được app | Chạy `RUN_DEBUG.bat` để xem lỗi; chạy lại `INSTALL.bat` nếu thiếu thư viện |
| "Không thể kết nối OBS" | OBS chưa mở, chưa bật WebSocket server, sai port/mật khẩu. Bấm *Kiểm tra* để xem gợi ý |
| Kịch bản AI lỗi 404 model | Cài đặt → *Tải danh sách model* → chọn model đang khả dụng với key của bạn |
| AI báo API key không hợp lệ | Copy lại key đầy đủ từ Google AI Studio (không copy đoạn `...xxxx`) |
| Không tạo được audio | Edge TTS cần Internet; kiểm tra mạng/proxy, thử lại sau vài giây |
| Video không hiện trong OBS | Chưa bấm *Tạo scene & nguồn LiveFlow*, hoặc mục chưa gắn video/ảnh (xem cột Video trong Playlist) |
| Không có tiếng trên stream | Chọn *Media source trong OBS* cho âm thanh, hoặc bật *Desktop Audio* trong OBS Mixer |
| OBS không bắt đầu stream | Sai Stream Key/Server, mạng yếu, hoặc phòng LIVE chưa mở trên nền tảng |
| Thời lượng video hiện "—" | Chưa có FFmpeg; chạy `scripts\download_ffmpeg.ps1` hoặc chọn thư mục FFmpeg trong Cài đặt |

---

*LiveFlow AI Studio — LIVE bán hàng tự động bằng AI cho TikTok và Shopee, phát qua OBS, không bịa thông tin, không giả lập thành công.*
