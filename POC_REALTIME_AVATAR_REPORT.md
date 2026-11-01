# Báo Cáo Kiểm Chứng Kỹ Thuật POC: Avatar Realtime Lên OBS Studio

## 1. Giới thiệu & Bối cảnh

Báo cáo này ghi nhận toàn bộ quá trình thiết kế, triển khai và kiểm chứng kỹ thuật cho hệ thống **Người Dẫn Ảo Livestream Bán Hàng (AI Virtual Livestream Seller)** theo đúng các nguyên tắc và tiêu chí nghiệm thu đề ra:

1. **Không sử dụng video dựng sẵn / ảnh nhép miệng làm avatar realtime.**
2. **Không đưa Veo vào vòng lặp phản hồi realtime.**
3. **Không để AI tự đoán giá, khuyến mãi, tồn kho hay công dụng.**
4. **Kiểm chứng trực tiếp luồng phát 1080×1920 (9:16) lên OBS Studio trước khi mở rộng giao diện.**

---

## 2. Kiến trúc giải pháp đã triển khai

```text
┌───────────────────────────────────────────────┐
│       Sản phẩm + Chính sách đã xác minh       │
└──────────────────────┬────────────────────────┘
                       │
                       ▼
         [ ProductKnowledgeService ]
   (Kiểm định nghiêm ngặt, Zero-Hallucination)
                       │
                       ▼
          [ LiveSessionOrchestrator ]
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
  [Comment Policy] [TTS Engine] [OBS Compositor]
  (OCR/API/Manual) (Edge-TTS)   (1080x1920 9:16)
         │             │             │
         └─────────────┼─────────────┘
                       │
                       ▼
            [ RealtimeBridgeServer ]
             http://127.0.0.1:8088
           ┌───────────┴───────────┐
           ▼                       ▼
    /avatar.html             /overlay.html
  (WebRTC/Canvas)           (Card SKU + CTA)
  (Nền trong suốt)          (Comment Toast)
           │                       │
           └───────────┬───────────┘
                       │
                       ▼
             [ OBS Browser Sources ]
       (Tự động điều hướng Audio vào Mixer)
                       │
                       ▼
              [ OBS Canvas 9:16 ]
                       │
                       ▼
                  RTMP Stream
```

---

## 3. Các module cốt lõi đã hoàn thành

### 3.1. ProductKnowledgeService (`app/services/product_knowledge_service.py`)
- **Trách nhiệm:** Trích xuất hồ sơ sản phẩm đã xác minh (`VerifiedProductProfile`), kiểm tra tính hợp lệ của giá (>0) và tối thiểu 1 đặc tính kỹ thuật đã kiểm chứng.
- **Rào chắn chống bịa đặt (Anti-Hallucination Guardrail):**
  - Quét mọi số liệu tiền tệ trong câu nói để đối chiếu với `price`, `promo_price` hoặc giá biến thể. Nếu có số tiền lạ không khớp -> Cảnh báo / Từ chối.
  - Chặn các từ cấm y tế, cam kết tuyệt đối: *"trị dứt điểm"*, *"chữa khỏi hoàn toàn"*, *"cam kết 100%"*, *"thuốc tiên"*.
  - Sinh system prompt chuẩn xác cho LLM với các ràng buộc phủ định nghiêm ngặt.

### 3.2. RealtimeBridgeServer (`app/avatar_realtime/bridge_server.py`)
- **Trách nhiệm:** Cung cấp máy chủ nhúng HTTP & WebSocket siêu nhẹ tại `127.0.0.1:8088`.
- **Đầu ra OBS Browser Source:**
  - `http://127.0.0.1:8088/avatar.html`: Render nhân vật ảo 1080×1920 với **nền trong suốt tuyệt đối (`rgba(0, 0, 0, 0)`)**, không bị răng cưa/lem màu như Chroma key xanh lá truyền thống. Hỗ trợ cử động thở, chớp mắt, lắc nhẹ tự nhiên và nhép môi đồng bộ theo tần số âm thanh tiếng Việt.
  - `http://127.0.0.1:8088/overlay.html`: Lớp đồ hoạ 9:16 chuyên nghiệp cho livestream gồm:
    - Huy hiệu "● TIKTOK LIVE", "✓ CHÍNH HÃNG 100%".
    - Thẻ sản phẩm SKU: Hiển thị ảnh, tên, giá gốc, giá khuyến mãi và % giảm giá.
    - Hộp thông báo bình luận: Trượt vào khi MC trả lời câu hỏi của khách và tự động biến mất.
    - Thanh kêu gọi hành động (Call To Action) nhấp nháy thu hút mua sắm.

### 3.3. Realtime Avatar Providers (`app/avatar_realtime/`)
- `LocalMockRealtimeProvider`: Sinh giọng đọc tiếng Việt thời gian thực bằng `edge-tts` (giọng Hoài My / Nam Minh), truyền luồng âm thanh và visemes qua WebSocket trực tiếp vào OBS. Cho phép kiểm thử 60 phút không tốn chi phí credit API.
- `LiveAvatarLiteProvider`: Tích hợp chuẩn chế độ LITE của LiveAvatar qua WebRTC / LiveKit.
- `create_realtime_avatar_provider`: Factory linh hoạt chuyển đổi theo cấu hình môi trường `.env`.

### 3.4. CommentPolicyEngine (`app/comments/policy_engine.py`)
- **Khử trùng lặp (Deduplication):** Chống spam lặp lại cùng câu hỏi trong vòng 20 giây.
- **Gating theo độ tin cậy:** OCR có độ tin cậy < 0.75 sẽ bị từ chối tự động trả lời để tránh nhận diện sai chữ.
- **Phân loại ý định:** Nhận diện các nhóm: Giá cả (`price`), Biến thể (`variant`), Vận chuyển (`shipping`), Hướng dẫn (`usage`), Công dụng (`feature`), Khiếu nại (`complaint`), Spam (`spam`).
- **Xác thực dữ kiện:**
  - Khiếu nại hoặc câu hỏi ngoài phạm vi dữ liệu -> Đưa vào Hàng chờ Người Vận Hành (`operator_queue`).
  - Câu hỏi an toàn có dữ liệu -> Tự động sinh câu thoại chuẩn tiếng Việt, gửi tới Avatar nói trực tiếp và hiện Toast trên OBS.

### 3.5. OBS Compositor Tự Động (`app/playout/obs_compositor.py`)
- Bổ sung phương thức `setup_realtime_scene()`:
  - Khóa chặt Canvas 1080×1920 (9:16) @ 30 FPS.
  - Tự động tạo 2 nguồn `browser_source` (Avatar + Overlay).
  - Tự động bật `reroute_audio = True` để âm thanh của MC đi thẳng vào Audio Mixer của OBS.
  - Căn chỉnh Z-order: Nền nền (0) → Avatar (1) → Overlay Card & CTA (2).

### 3.6. Script Kiểm Thử Độc Lập (`scripts/poc_realtime_avatar_obs.py`)
- Kịch bản chạy thử tự động từ đầu đến cuối:
  - Kiểm tra cổng OBS 4455 và tự cấu hình scene.
  - Nạp 3 sản phẩm mẫu đã xác minh.
  - Chạy vòng lặp MC giới thiệu sản phẩm.
  - Bắn thử 4 tình huống bình luận thực tế (Hỏi giá, Hỏi da nhạy cảm, Spam rác, Khiếu nại giao hàng).
  - Tổng kết bảng kiểm tra nghiệm thu.

---

## 4. Hướng dẫn chạy thử nghiệm thực tế

### Bước 1: Khởi động OBS Studio (Nếu muốn xem trên OBS)
1. Mở OBS Studio.
2. Vào **Công cụ (Tools)** → **Cài đặt máy chủ WebSocket (WebSocket Server Settings)**.
3. Tích chọn **Bật máy chủ WebSocket** (Port: `4455`, không đặt mật khẩu hoặc điền đúng mật khẩu vào `.env`).

### Bước 2: Chạy Script POC
Mở Terminal và thực thi lệnh:
```powershell
.\.venv\Scripts\python scripts/poc_realtime_avatar_obs.py --mode mock --duration 60
```

Nếu chưa bật OBS, bạn có thể chạy:
```powershell
.\.venv\Scripts\python scripts/poc_realtime_avatar_obs.py --mode mock --duration 60 --no-obs
```
Sau đó mở trình duyệt truy cập:
- `http://127.0.0.1:8088/avatar.html` (Xem người dẫn ảo nhép môi theo tiếng nói)
- `http://127.0.0.1:8088/overlay.html` (Xem thẻ sản phẩm và thông báo trả lời comment)
