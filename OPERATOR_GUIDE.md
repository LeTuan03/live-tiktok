# 📋 HƯỚNG DẪN VẬN HÀNH & CHECKLIST LÊN SÓNG LIVEFLOW (1 TRANG)

Tài liệu hướng dẫn tinh gọn dành cho người vận hành phiên Livestream bán hàng tự động trên TikTok / Shopee.

---

## ⚡ 1. CHECKLIST 5 PHÚT TRƯỚC GIỜ LÊN SÓNG (BẮT BUỘC)

Thực hiện tuần tự 7 bước trước khi ấn nút Bắt đầu phát:

- [ ] **1. Mở OBS Studio & Bật WebSocket 5:**
  - Mở OBS Studio trên máy tính.
  - Vào menu: `Công cụ (Tools)` → `Thiết đặt máy chủ WebSocket (WebSocket Server Settings)`.
  - Đảm bảo ô **Bật máy chủ WebSocket** được tích chọn (Cổng mặc định: `4455`).
- [ ] **2. Chạy Bác sĩ kiểm tra (`tools\doctor.bat`):**
  - Chạy file `doctor.bat` trên màn hình desktop.
  - Đảm bảo tất cả các mục đều có icon ✅ (Python, FFmpeg, Phông chữ, Ổ đĩa > 10GB, Mạng).
- [ ] **3. Kiểm tra Scene chuẩn 9:16:**
  - Độ phân giải Base (Canvas): **1080 x 1920** (chuẩn khung dọc TikTok/Shopee).
  - Khung hình: **30 FPS**.
  - Bộ mã hóa: **x264 (CPU)**, Bitrate video: **6000 kbps**, Keyframe: **2s**, Profile: `high`.
- [ ] **4. Nạp RTMP Server & Khóa luồng (Stream Key):**
  - Vào TikTok Live Studio hoặc Shopee Live lấy `Server URL` và `Stream Key`.
  - Dán vào tab **Live Studio** trong ứng dụng LiveFlow và bấm **Áp dụng RTMP**.
- [ ] **5. Chọn Kịch bản & Danh sách phát (Playlist):**
  - Vào tab **Live Studio** → Chọn Playlist đã chuẩn bị các sản phẩm cho buổi LIVE hôm nay.
  - Kiểm tra các sản phẩm đã có video review dựng sẵn hoặc ảnh chất lượng cao.
- [ ] **6. Bật Chế độ Giữ Mắt (Filler Clip Dự Phòng):**
  - Đảm bảo tính năng **Standby Filler Fallback** đã bật trong cài đặt.
  - Khi có sự cố mạng hoặc file video thiếu, màn hình sẽ tự phát card chờ chuyên nghiệp thay vì màn hình đen.
- [ ] **7. Kiểm tra Vùng OCR Chat (Bình luận trực tiếp):**
  - Kéo cửa sổ Live Studio / TikTok sang cạnh bên.
  - Đặt vùng chụp Screen OCR đúng khung chat để hệ thống tự động trả lời giá, size, vận chuyển.

---

## 🎬 2. QUY TRÌNH VẬN HÀNH TRONG PHIÊN LIVE

| Tình huống | Thao tác trên LiveFlow |
|---|---|
| **Bắt đầu phiên** | 1. Bấm `Bắt đầu Điều phối` trong Live Studio.<br>2. Bấm `Bắt đầu Stream` trong OBS Studio. |
| **Khách hỏi Giá / Size / Ship** | Hệ thống tự động nhận diện qua OCR và chèn câu trả lời có căn cứ (`NOD_LISTEN` clip + giọng đọc) vào giữa nhịp video. |
| **Deal Chớp Nhoáng (Flash Sale)** | Bấm `⚡ Chèn Deal Sốc` trong bảng điều khiển để ưu tiên phát sản phẩm giảm giá ngay lập tức. |
| **Tạm dừng khi có việc riêng** | Bấm `⏸️ Tạm dừng` → Hệ thống sẽ tự động phát loop clip Chờ Standby êm ái, giữ chân người xem. |
| **Khách hỏi câu hỏi đặc biệt** | Nhập nội dung vào ô **Trả lời nhanh** ở tab Bình luận → Bấm **Phát ngay** để MC AI đọc câu trả lời. |
| **Kết thúc phiên LIVE** | 1. Bấm `Dừng Stream` trong Live Studio.<br>2. Hệ thống sẽ tự động xuất **Báo cáo phiên LIVE** (doanh số, số bình luận đã giải đáp, tỷ lệ giữ mắt). |

---

## 🆘 3. XỬ LÝ SỰ CỐ NHANH (TROUBLESHOOTING)

- **Màn hình OBS bị đen hoặc không nhận video:**
  - Kiểm tra xem tên nguồn media trong OBS có đúng là `LiveFlow_Media` không.
  - Vào tab Live Studio bấm `Dựng lại Scene OBS tự động`.
- **OBS báo mất kết nối WebSocket:**
  - Kiểm tra OBS Studio có đang mở không. Bấm `Kết nối lại OBS` trong tab Live Studio.
- **Tiếng không phát ra livestream:**
  - Trong OBS Studio: Click chuột phải vào `LiveFlow_Media` → `Thuộc tính âm thanh nâng cao (Advanced Audio Properties)` → Chuyển sang `Giám sát và xuất (Monitor and Output)`.
- **Cần hỗ trợ kỹ thuật:**
  - Mở menu Cài đặt → Bấm nút **"Xuất gói Log hỗ trợ"** (`liveflow_log_package.zip`) và gửi cho đội ngũ kỹ thuật.
