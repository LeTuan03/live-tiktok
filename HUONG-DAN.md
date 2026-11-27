# Hướng dẫn cài và chạy SoShop

SoShop chạy hoàn toàn trên máy của bạn. Trên Windows chỉ cần **một file cài đặt**: không cần Docker, không cần cài Python hay cơ sở dữ liệu, không cần quyền quản trị.

## Cần chuẩn bị

| Thứ cần có | Ghi chú |
|---|---|
| Máy Windows 10/11 64-bit, RAM từ 8 GB (khuyên dùng 16 GB) | Còn trống khoảng 6 GB ổ đĩa, thêm chỗ cho video |
| File **`SoShop-Setup-<phiên bản>.exe`** | Người phát hành gửi cho bạn (Google Drive, USB...) |
| Groq API key (miễn phí) | Lấy tại [console.groq.com/keys](https://console.groq.com/keys) |
| Google Chrome | Chỉ cần nếu tạo video sản phẩm bằng Google Flow |

## Cài đặt (khoảng 5 phút)

1. Bấm đúp **`SoShop-Setup-<phiên bản>.exe`**.
   - Nếu Windows hiện "Windows protected your PC" (Windows đã bảo vệ máy tính), bấm **More info** (Thông tin thêm) rồi **Run anyway** (Vẫn chạy). Bộ cài chưa được ký số nên Windows hỏi lại một lần.
2. Bấm **Cài đặt**, chờ thanh tiến trình chạy hết.
3. Bấm **Mở SoShop**.
4. Lần đầu, SoShop hỏi Groq API key: dán key rồi bấm **Lưu**.
5. Chờ các dòng trạng thái chuyển sang màu xanh (lần đầu khoảng 1 đến 2 phút). Trình duyệt tự mở **Studio** (http://localhost:3000/studio).

Lần sau chỉ cần bấm biểu tượng **SoShop** trên Desktop hoặc trong menu Start.

## Cửa sổ SoShop

| Nút | Dùng để |
|---|---|
| **Mở Studio** | Mở trang quản lý sản phẩm, video và phiên live trong trình duyệt |
| **Kết nối Google Flow** | Hướng dẫn từng bước cài tiện ích Chrome để tạo video bằng Flow |
| **Cài đặt** | Đổi Groq key, giọng đọc, key ElevenLabs hoặc LemonSlice |
| **Sao lưu dữ liệu** | Xuất toàn bộ sản phẩm, video, phiên live ra một file `.zip` |
| **Khôi phục dữ liệu** | Nạp lại dữ liệu từ file `.zip` đó (ví dụ trên máy mới) |
| **Nhật ký** | Mở thư mục nhật ký khi cần báo lỗi |

**Đóng cửa sổ SoShop là tắt SoShop.** Studio và màn hình phát live chỉ chạy khi cửa sổ này đang mở (có thể thu nhỏ). Dữ liệu luôn được giữ lại.

Trong Studio:

- **Sản phẩm** (`/studio/products`): thêm sản phẩm, ảnh, giá, điểm nổi bật.
- **Video** (`/studio/videos`): tạo video quảng cáo cho từng sản phẩm bằng Google Flow.
- **Phiên live** (`/studio/live`): xếp các video thành playlist, bấm phát, tạm dừng, chuyển sản phẩm. Màn hình phát dọc 9:16 nằm ở `/onair/<mã phiên>`, đưa vào OBS (Browser Source) để phát lên TikTok hoặc YouTube.

## Kết nối Google Flow (mỗi máy làm một lần)

SoShop điều khiển Google Flow ngay trong Chrome của bạn và dùng credit Flow trong tài khoản Google của bạn. Bấm **Kết nối Google Flow** trong cửa sổ SoShop và làm theo 4 bước hiện ra:

1. Bấm **Mở trang Tiện ích của Chrome** (đường dẫn thư mục tiện ích được chép sẵn).
2. Bật **Chế độ dành cho nhà phát triển** (Developer mode) ở góc trên bên phải.
3. Bấm **Tải tiện ích đã giải nén** (Load unpacked), dán đường dẫn vào ô địa chỉ của hộp chọn thư mục, bấm Enter rồi **Chọn thư mục**.
4. Đăng nhập Google Flow trong Chrome đó.

Khi dòng **Google Flow** trong cửa sổ SoShop chuyển sang xanh là xong. Giữ Chrome mở khi SoShop đang tạo video.

## Chuyển dữ liệu sang máy khác

1. Ở máy cũ: bấm **Sao lưu dữ liệu**, lưu file `SoShop-du-lieu-<ngày>.zip`.
2. Chép file đó sang máy mới (USB, Google Drive...).
3. Ở máy mới: cài SoShop, bấm **Khôi phục dữ liệu**, chọn file vừa chép.

Máy cũ đang chạy bản Docker hoặc bản mã nguồn (`dev.ps1`)? Xuất dữ liệu bằng lệnh sau trong thư mục mã nguồn, rồi khôi phục ở máy mới như bước 3:

```powershell
powershell -ExecutionPolicy Bypass -File tools\export-data.ps1               # bản mã nguồn (dev.ps1)
powershell -ExecutionPolicy Bypass -File tools\export-data.ps1 -From docker  # bản Docker
```

## Cập nhật bản mới

Bấm đúp file `SoShop-Setup-<phiên bản mới>.exe` và cài đè (đóng cửa sổ SoShop trước). Dữ liệu, cài đặt và tiện ích Chrome vẫn giữ nguyên.

## Gặp sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| "Các cổng sau đang bị chương trình khác dùng" | Tắt chương trình được nêu tên (ví dụ bản SoShop chạy bằng Docker), rồi bấm **Thử khởi động lại** |
| Windows Firewall hỏi quyền cho `livekit-server` | Bấm **Allow** hoặc **Cancel** đều được: SoShop chỉ dùng mạng nội bộ trong máy |
| Studio mở được nhưng AI không nói, không viết được kịch bản | Bấm **Cài đặt**, kiểm tra Groq API key |
| Dòng **Google Flow** vẫn xám | Làm lại các bước ở mục Kết nối Google Flow. Trong `chrome://extensions`, bấm biểu tượng tải lại của tiện ích SoShop Flow Bridge |
| Một dòng trạng thái báo **Lỗi** | Bấm **Nhật ký**, gửi các file `.log` trong thư mục đó cho người hỗ trợ |
| Gỡ SoShop | Settings, Apps, Installed apps, chọn **SoShop**, Uninstall. SoShop hỏi có xóa dữ liệu hay giữ lại |

Dữ liệu nằm ở `%LOCALAPPDATA%\SoShop` (bấm "Mở thư mục dữ liệu" ở cuối cửa sổ SoShop).

## Bản Docker (macOS, Linux, hoặc máy đã có Docker)

Bản Docker vẫn dùng được: cài Docker Desktop, giải nén `SoShop-<phiên bản>.zip`, bấm đúp `soshop-start.cmd` (Windows) hoặc sao chép `soshop.env.example` thành `soshop.env`, điền key rồi chạy `docker compose up -d` (macOS, Linux), sau đó mở http://localhost:3000/studio. Tắt bằng `soshop-stop.cmd` hoặc `docker compose down`. Xóa sạch dữ liệu bằng `docker compose down -v`.

## Dành cho người phát hành

**Bộ cài Windows (khuyên dùng):**

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build-app.ps1 -Version 1.0.0
```

Kết quả là một file `dist\desktop\SoShop-Setup-1.0.0.exe` (khoảng 0,5 đến 1 GB) chứa đủ mọi thứ, code Python chỉ còn bytecode. Gửi đúng file đó. Chi tiết trong `desktop\README.md`.

**Bản Docker:** `powershell -ExecutionPolicy Bypass -File tools\build-release.ps1 -Version 1.0.0` build image, đẩy lên GHCR và tạo `dist\SoShop-1.0.0.zip` (cần `docker login ghcr.io` bằng token có quyền `write:packages`; nếu package để private, cấp cho người dùng token chỉ có quyền `read:packages`). Thêm `-Offline` để đóng luôn image vào zip (vài GB), người dùng không cần token hay mạng nhanh.
