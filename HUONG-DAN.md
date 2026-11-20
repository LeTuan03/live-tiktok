# Hướng dẫn cài và chạy SoShop

SoShop chạy hoàn toàn trên máy của bạn. Chỉ cần cài **Docker Desktop**, mọi thứ còn lại (cơ sở dữ liệu, máy chủ live, AI dẫn live, giao diện web) được tự động cài khi chạy lần đầu.

## Cần chuẩn bị

| Thứ cần có | Ghi chú |
|---|---|
| Máy Windows 10/11, RAM từ 8 GB (khuyên dùng 16 GB) | Còn trống khoảng 15 GB ổ đĩa |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Cài xong, mở lên một lần và đồng ý điều khoản |
| Groq API key (miễn phí) | Lấy tại [console.groq.com/keys](https://console.groq.com/keys) |
| Google Chrome | Chỉ cần nếu muốn tạo video sản phẩm bằng Google Flow |

## Chạy lần đầu

1. Giải nén file **`SoShop-<phiên bản>.zip`** được gửi cho bạn, ví dụ vào `D:\SoShop`.
2. Bấm đúp **`soshop-start.cmd`**.
3. Khi được hỏi, dán Groq API key rồi bấm Enter.
4. Nếu được hỏi **Token**, dán token mà người gửi bộ cài cấp cho bạn (chỉ hỏi một lần).
5. Chờ tải bộ cài (khoảng 3 đến 4 GB). Lần đầu mất khoảng 10 đến 20 phút tùy tốc độ mạng. Các lần sau chỉ vài giây.
6. Trình duyệt tự mở **http://localhost:3000/studio**. Xong.

Lần đầu mở một phiên live, máy tải thêm model avatar (khoảng 900 MB), nên hình có thể chờ vài phút mới lên.

## Dùng hằng ngày

| Muốn làm gì | Cách làm |
|---|---|
| Bật SoShop | Bấm đúp `soshop-start.cmd` |
| Tắt SoShop | Bấm đúp `soshop-stop.cmd` (dữ liệu vẫn được giữ) |
| Mở studio | http://localhost:3000/studio |
| Xem feed như người mua | http://localhost:3000 |

Trong studio:

- **Sản phẩm** (`/studio/products`): thêm sản phẩm, ảnh, giá, điểm nổi bật.
- **Video** (`/studio/videos`): tạo video quảng cáo cho từng sản phẩm bằng Google Flow (xem bên dưới).
- **Phiên live** (`/studio/live`): xếp các video thành playlist, bấm phát, tạm dừng, chuyển sản phẩm. Màn hình phát dọc 9:16 nằm ở `/onair/<mã phiên>`, có thể đưa vào OBS (Browser Source) để phát lên TikTok hoặc YouTube.

## Tạo video bằng Google Flow (tùy chọn)

SoShop điều khiển Google Flow trong Chrome của bạn, dùng credit Flow trong tài khoản Google của bạn.

1. Mở Chrome, vào `chrome://extensions`, bật **Developer mode** (góc trên bên phải).
2. Bấm **Load unpacked** và chọn thư mục `flow_extension` trong thư mục SoShop (bản mã nguồn: `server\flow_extension`).
3. Đăng nhập Google Flow (https://flow.google.com) trong Chrome đó.
4. Giữ Chrome mở khi SoShop đang tạo video.

## Đổi cài đặt

Mọi cài đặt nằm trong file **`soshop.env`** (tạo tự động ở lần chạy đầu). Mở bằng Notepad, sửa, lưu lại, rồi bấm đúp `soshop-start.cmd` để áp dụng.

| Cài đặt | Ý nghĩa |
|---|---|
| `GROQ_API_KEY` | Key Groq, bắt buộc để AI nói và viết kịch bản |
| `EDGE_TTS_VOICE` | Giọng đọc miễn phí. `google-vi` là tiếng Việt |
| `ELEVEN_API_KEY` | Giọng ElevenLabs (trả phí), bỏ trống nếu không dùng |
| `LEMONSLICE_API_KEY` | Avatar LemonSlice (trả phí), bỏ trống để dùng avatar có sẵn |
| `CHANNEL_MIN_SLOT_SECONDS` | Giữ `20` với Groq miễn phí để không vượt giới hạn |

## Cập nhật bản mới

Nhận file zip bản mới, giải nén **đè lên thư mục cũ** (giữ nguyên file `soshop.env`), rồi bấm đúp `soshop-start.cmd`. SoShop tự tải phần mới và cập nhật cơ sở dữ liệu, dữ liệu cũ vẫn còn.

## Gặp sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| "Chưa cài Docker Desktop" | Cài Docker Desktop, mở lên một lần, chạy lại `soshop-start.cmd` |
| "Các cổng sau đang bị chương trình khác dùng" | Tắt chương trình đang dùng cổng 3000, 8000 hoặc 7880, rồi chạy lại |
| Studio mở được nhưng AI không nói | Kiểm tra `GROQ_API_KEY` trong `soshop.env`, rồi chạy lại `soshop-start.cmd` |
| Phiên live không có hình | Chờ vài phút ở lần đầu (đang tải model avatar). Vẫn không được thì tắt rồi bật lại SoShop |
| Muốn xem nhật ký lỗi | Mở PowerShell trong thư mục SoShop, chạy `powershell -File tools\soshop.ps1 logs` |
| Muốn xóa sạch dữ liệu, làm lại từ đầu | Chạy `docker compose down -v` trong thư mục SoShop (xóa toàn bộ sản phẩm, video, phiên live) |

Dùng macOS hoặc Linux: cài Docker, sao chép `soshop.env.example` thành `soshop.env`, điền key, rồi chạy `docker compose up -d` trong thư mục SoShop và mở http://localhost:3000/studio.

## Dành cho người phát hành

Từ bản mã nguồn, chạy `powershell -ExecutionPolicy Bypass -File tools\build-release.ps1 -Version 1.0.0`. Script build image (code Python chỉ còn bytecode), đẩy lên GHCR và tạo `dist\SoShop-1.0.0.zip` để gửi cho người dùng. Trước đó cần `docker login ghcr.io` bằng GitHub token có quyền `write:packages`. Nếu package để private, cấp cho người dùng một token chỉ có quyền `read:packages`.

Không muốn dùng GHCR: thêm `-Offline`. Bộ cài (vài GB) được đóng luôn vào zip, người dùng không cần token và không cần tải gì thêm khi cài.

Gửi bản build, không gửi code
Mình vừa thêm chế độ -Offline, đúng với ý "gửi bản build". Sau khi Docker ổn, bạn chạy:


powershell -ExecutionPolicy Bypass -File tools\build-release.ps1 -Version 1.0.0 -Offline
Kết quả là file dist\SoShop-1.0.0.zip (vài GB) gồm:

images\soshop-images.tar: bản build (server chỉ còn bytecode .pyc, web là JS đã build)
soshop-start.cmd, soshop-stop.cmd, docker-compose.yml, soshop.env.example
flow_extension\ (extension Chrome) và HUONG-DAN.md
Gửi file zip qua Google Drive hoặc USB. Máy nhận làm như sau:

Cài Docker Desktop.
Giải nén, bấm đúp soshop-start.cmd, nhập Groq key. Script tự nạp bản build từ file .tar: không cần mạng để tải, không cần token, mất vài phút.
Cài extension: vào chrome://extensions, bật Developer mode, bấm Load unpacked và chọn thư mục flow_extension.