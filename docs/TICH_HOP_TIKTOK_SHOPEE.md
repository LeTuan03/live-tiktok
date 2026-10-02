# Tích hợp TikTok LIVE & Shopee Live — phạm vi và giới hạn

LiveFlow AI Studio dùng kiến trúc **capability-based**: mỗi tính năng nền tảng có trạng thái rõ ràng,
ứng dụng không bao giờ báo "thành công" cho thao tác mà nền tảng chưa xác nhận.

| Trạng thái | Ý nghĩa |
|---|---|
| ✅ Hỗ trợ | Đã triển khai bằng phương thức được phép và có xác nhận từ nền tảng/OBS |
| 🖐️ Thủ công | Người vận hành thao tác trên app chính thức; LiveFlow hỗ trợ (gợi ý, sao chép, giọng đọc, nhật ký) |
| 🔐 Cần quyền/API | Có API chính thức nhưng cần đăng ký đối tác/được duyệt — chưa tích hợp |
| ⛔ Không hỗ trợ | Không có phương thức hợp lệ |

## Ma trận hiện tại (v1.0.0)

| Tính năng | TikTok LIVE | Shopee Live | Cách thực hiện trong LiveFlow |
|---|---|---|---|
| Phát video/âm thanh lên LIVE | ✅ qua OBS RTMP | ✅ qua OBS RTMP | Live Studio → nhập Server URL + Stream Key → "Gửi cấu hình RTMP vào OBS" → "Bắt đầu LIVE" |
| Tạo/mở phòng LIVE | 🖐️ | 🖐️ | Mở phòng trên app/LIVE Studio chính thức trước, rồi bắt đầu stream từ OBS |
| Đọc bình luận thời gian thực | 🖐️ | 🖐️ | Dán bình luận vào "Trợ lý bình luận" (từng dòng hoặc `tên: nội dung`) |
| Gửi trả lời bình luận | 🖐️ | 🖐️ | Duyệt câu trả lời → Sao chép dán lên app, hoặc "🔊 Đọc trên LIVE" → đánh dấu "Đã gửi thủ công" |
| Ghim sản phẩm | 🖐️ | 🖐️ | Ghim trong app; LiveFlow chỉ cho đánh dấu "đã ghim" |
| Đọc đơn hàng | 🔐 | 🔐 | Theo dõi trong Seller Center / Kênh Người Bán |

## Lấy Stream Key

### TikTok LIVE
1. Dùng **TikTok LIVE Studio** (ứng dụng PC) hoặc **LIVE Center** trên web → *Go LIVE* → chọn
   *Phần mềm phát trực tiếp bên thứ ba (OBS)*.
2. Sao chép **Server URL** và **Stream Key**. TikTok chỉ cấp cho tài khoản đủ điều kiện (thường ≥ 1.000
   người theo dõi); điều kiện do TikTok quy định và có thể thay đổi.
3. Dán vào LiveFlow → Live Studio → *Gửi cấu hình RTMP vào OBS* (hoặc tự dán vào OBS → Settings → Stream →
   Service: Custom).
4. Bấm **Bắt đầu LIVE** (OBS Start Streaming). Kiểm tra phòng LIVE trên TikTok.

### Shopee Live
1. App Shopee → **Shopee Live** → *Tạo phiên live* → chọn *Phát qua phần mềm/thiết bị khác (OBS)* nếu shop
   đủ điều kiện.
2. Sao chép **Stream URL** và **Stream Key** hiển thị trong app.
3. Dán vào LiveFlow → Live Studio (chọn nền tảng *Shopee Live*) → *Gửi cấu hình RTMP vào OBS*.
4. Bấm **Bắt đầu LIVE** trong LiveFlow rồi xác nhận *Bắt đầu* trong app Shopee.

## Những điều LiveFlow KHÔNG làm (theo thiết kế)
- Không scrape API riêng tư, không reverse-engineer giao thức TikTok/Shopee.
- Không lưu mật khẩu TikTok/Shopee; đăng nhập luôn thực hiện trên app chính thức.
- Không vượt CAPTCHA/OTP, không tự động hóa trình duyệt để giả mạo thao tác người dùng.
- Không tự động gửi bình luận/ghim sản phẩm (auto-reply bị khóa cho đến khi có API hợp lệ).
- Không bịa thông tin sản phẩm: AI chỉ dùng dữ liệu bạn đã lưu + chính sách shop đã khai báo; thiếu dữ liệu
  thì trả lời "em kiểm tra lại".

## Mở rộng trong tương lai
Khi có TikTok Shop Open API / Shopee Open Platform được duyệt, chỉ cần thêm provider trong
`app/integrations/tiktok/` và đổi trạng thái capability tương ứng; UI và luồng duyệt đã sẵn sàng.
