# 🎞️ Chế độ Google Flow — tạo video người thật ngay trong LiveFlow

Dành cho người đang có **tín dụng Google Flow** (gói Google AI Pro / Ultra).
Chế độ này **đã được bật sẵn** trong `.env`: `LIVEFLOW_VIDEO_SOURCE=flow`.

---

## 1. Vì sao có chế độ này

| | Gọi API Veo | **Chế độ Flow (đang bật)** |
|---|---|---|
| Cần API key | ✅ | ❌ không cần |
| Cần bật Billing Google Cloud | ✅ | ❌ không cần |
| Trả tiền kiểu gì | theo giây video | **trừ tín dụng gói Pro bạn đã trả** |
| Làm ở đâu | ngầm trong app | **tab 🎞️ Google Flow trong app** |

Flow **không có API công khai** — Google chỉ cho dùng qua trang web. Vì vậy LiveFlow
**nhúng thẳng flow.google.com vào trong app** và tự bấm hộ bạn:

```
LiveFlow viết prompt → tự dán vào Flow → tự bấm Tạo → tự chờ render
→ tự tải clip về → tự chuẩn hoá 1080x1920@30 → tự nạp vào thư viện/dự án
→ tự trừ tín dụng đã dùng
```

Bạn chỉ làm **2 việc**: đăng nhập Google một lần, rồi bấm **▶️ Chạy tự động**.

---

## 2. Cài một lần (3 bước)

1. Mở LiveFlow → vào tab **🎞️ Google Flow** (nằm ngay dưới *Người mẫu AI*).
2. Khung bên phải là trang Flow. **Đăng nhập Google** như bình thường.
   App ghi nhớ đăng nhập, lần sau mở là đã vào sẵn.
3. Trong Flow, bấm **tạo một Project mới** rồi để đó. Xong.

> Dòng trạng thái trên khung Flow phải hiện: `✅ đã đăng nhập · ✅ thấy ô prompt · ✅ thấy nút Tạo`.
> Thiếu cái nào xem mục [6. Xử lý sự cố](#6-xử-lý-sự-cố).

**Đối chiếu số dư:** bấm **🔄 Đồng bộ số dư**, nhập đúng số tín dụng đang hiện trên
Flow (ví dụ `1050`). Từ đó app tự trừ dần sau mỗi clip.

---

## 3. Dùng hằng ngày

### 3.1. Tạo người mẫu AI (làm một lần, dùng mãi)

Tab **💃 Người mẫu AI** → **🎞️ Tạo người mẫu bằng Google Flow**
(hoặc tab Flow → **💃 Gói người mẫu**).

Điền tên, giới tính, ngoại hình → app dựng **10 việc**:

| Việc | Dùng để làm gì | Tín dụng (Fast) |
|---|---|---|
| 1 ảnh chân dung | kéo vào *Ingredients* để mặt không đổi giữa các clip | ~10 |
| 1 clip nền (loop) | phát suốt phiên live khi không có cử chỉ đặc biệt | 20 |
| 8 clip cử chỉ | WAVE_GREET, HOLD_PRODUCT, SHOW_DETAIL, POINT_PRICE, TRY_ON, CTA_POINT_DOWN, NOD_LISTEN, SURPRISE | 160 |
| | **Tổng** | **~190** |

Bấm **▶️ Chạy tự động cả gói** rồi để app làm. Nhật ký bên dưới chạy từng dòng:

```
[02] Clip nền (loop) — Đứng thuyết minh — đang dán prompt vào Flow...
   đã dán prompt.
   đã bấm Tạo (click) — trừ 20 tín dụng, còn 1030. Đang chờ Flow render...
   Flow đã render xong, đang tải clip về...
   ✅ Đã nạp clip nền (loop) cho người mẫu → mai_phuong.mp4
```

Clip cử chỉ được nạp ở trạng thái **CHƯA duyệt** — vào **🎬 Cử chỉ & Duyệt (9 Cues)**
xem lại rồi bấm **Duyệt**. LiveFlow chỉ phát clip đã duyệt khi lên sóng.

> 💡 **Giữ mặt người mẫu giống nhau:** làm việc số 01 (ảnh chân dung) trước, tải ảnh về,
> rồi kéo ảnh đó vào ô **Ingredients** của Flow. Mọi clip sau dùng chung ảnh này.
> Bỏ qua bước này thì mặt sẽ khác nhau giữa các clip — người xem nhận ra ngay.

### 3.2. Tạo video bán hàng cho một sản phẩm

Tab **📦 Sản phẩm** → chọn sản phẩm → **✨ Video AI (Google Flow)**.

App sẽ: cho AI viết kịch bản 3 cảnh (HOOK → DEMO → CTA) bám đúng dữ liệu sản phẩm →
tạo giọng đọc dự phòng → ghép khung hình đầu (người mẫu + sản phẩm) → dựng gói việc.

Bấm **▶️ Chạy tự động**, chờ 3 cảnh xong, rồi bấm **🎬 Ghép video hoàn chỉnh**.
Video ra được gắn luôn vào sản phẩm, dùng được ngay trong Playlist / Live Studio.

Mặc định câu thoại tiếng Việt được **ghép vào prompt để model tự đọc** → mồm khớp lời.
Muốn dùng giọng Edge-TTS thay thế thì đặt `LIVEFLOW_VEO_USE_NATIVE_AUDIO=false`.

---

## 4. Tính tín dụng với 1.050 tín dụng

Giá Google công bố cho gói Pro (mỗi **lần tạo** = 1 video):

| Model trong Flow | Tín dụng | 1.050 tín dụng làm được |
|---|---|---|
| **Veo 3.1 – Fast** ⭐ | 20 | ~52 clip |
| Veo 3.1 – Quality | 100 | ~10 clip |
| Veo 3.1 – Lite (nếu tài khoản có) | 10 | ~105 clip |

Cách chia hợp lý cho 1.050 tín dụng:

```
190  → 1 người mẫu đầy đủ (ảnh + loop + 8 cử chỉ)   ← làm một lần, dùng mãi
860  → ~14 sản phẩm × 3 cảnh × 20 tín dụng
```

**3 điều làm bay tín dụng nhanh nhất:**

1. **`Outputs per prompt` để 2** → tốn gấp đôi cho cùng một prompt. Phải để **1**.
2. **Bấm Upscale 4K** → tốn thêm tín dụng mỗi video. Không cần: LiveFlow hạ về 1080x1920.
3. **Tạo lại nhiều lần** vì prompt chưa tốt → sửa prompt trước, đừng tạo lại mò.

Mỗi lần app bấm Tạo đều ghi vào sổ kèm nhãn việc; lệch với Flow thì bấm
**🔄 Đồng bộ số dư** nhập lại con số thật.

---

## 5. Những gì app làm thay bạn

| Bước | Ai làm |
|---|---|
| Viết prompt (khung 9:16, nền xanh #00FF00, không chữ, không zoom) | **App** |
| Đăng nhập Google | Bạn (một lần) |
| Dán prompt vào Flow, bấm Tạo | **App** |
| Chờ render, phát hiện clip mới | **App** |
| Tải clip về | **App** |
| Chuẩn hoá 1080x1920@30 h264, pad nền xanh | **App** |
| Đặt tên file, tạo ảnh đại diện, đăng ký 9 cue | **App** |
| Trừ tín dụng, lưu trạng thái gói việc | **App** |
| Kéo ảnh chân dung vào *Ingredients* | Bạn (một lần mỗi người mẫu) |
| **Xem lại và Duyệt clip trước khi lên sóng** | **Bạn** — app không tự duyệt |

---

## 6. Xử lý sự cố

| Hiện tượng | Nguyên nhân & cách xử lý |
|---|---|
| `⚠️ chưa đăng nhập` hoặc Google báo *"This browser or app may not be secure"* | Google chặn đăng nhập trên trình duyệt nhúng. Bấm **🌐 Mở bằng Chrome**, đăng nhập và tạo video ở Chrome — LiveFlow **vẫn tự nhận file bạn tải về** trong thư mục Downloads rồi chuẩn hoá và nạp như thường. |
| `⚠️ chưa thấy ô prompt` / `⚠️ Không dán được prompt` | Google vừa đổi giao diện Flow. Tạo file `flow_selectors.json` trong `%LOCALAPPDATA%\LiveFlowAIStudio\` (xem mẫu bên dưới) rồi mở lại app. Trong lúc chờ: bấm **📋 Dán prompt** / **⚡ Bấm Tạo** thủ công, hoặc dán tay vào khung Flow — các bước sau vẫn tự động. |
| `⚠️ Không tìm thấy nút Tạo` | Như trên. Bấm Tạo trực tiếp trong khung Flow — app vẫn tự nhận clip và nạp. |
| `⚠️ Hết thời gian chờ mà chưa thấy clip mới` | Flow render lâu hoặc đã hết tín dụng thật. Xem khung Flow có báo lỗi gì không, rồi bấm **⬇️ Tải clip**. Tăng chờ: `LIVEFLOW_FLOW_WAIT_TIMEOUT_SECONDS=900`. |
| `tải trực tiếp bị chặn` | Link video của Flow khác tên miền nên bị CORS chặn — app tự chuyển sang bấm nút **Download** của Flow. Không cần làm gì. |
| `Nền xanh chỉ 8% — quá ít` | Clip không ra nền xanh phẳng. Tạo lại; hoặc bỏ tích *Nền xanh* và dùng clip có bối cảnh studio (khi đó OBS không tách nền được). |
| `Khung NGANG 1280x720` | Trong Flow chưa đặt **Aspect ratio = 9:16 (Portrait)**. Đặt lại rồi tạo lại, nếu không clip sẽ bị thêm viền đen. |
| Mặt người mẫu khác nhau giữa các clip | Chưa kéo ảnh chân dung vào **Ingredients**. Xem mục 3.1. |
| Khung Flow trắng trơn / app nặng | Máy không có GPU tốt. `.env` đã đặt sẵn chế độ vẽ bằng CPU. Muốn thử GPU: `QTWEBENGINE_CHROMIUM_FLAGS=` (để trống) trong biến môi trường. |
| `⚠️ Không nạp được trình duyệt nhúng` | Môi trường chỉ có PySide6-Essentials. Chạy: `pip install PySide6-Addons`. |

**Mẫu `flow_selectors.json`** (chỉ cần điền cái nào bị sai):

```json
{
  "promptSelector":   "textarea[aria-label='Generate a video with text']",
  "generateSelector": "button[aria-label='Generate']",
  "videoSelector":    "video",
  "downloadSelector": "button[aria-label='Download']"
}
```

Lấy chuỗi này bằng cách bấm chuột phải vào ô prompt trong Chrome → *Inspect* → copy selector.

---

## 7. Cấu hình trong `.env`

```ini
LIVEFLOW_VIDEO_SOURCE=flow              # flow (dùng tín dụng Flow) | api (gọi Veo API, cần Billing)
LIVEFLOW_FLOW_MODEL=veo-3.1-fast        # veo-3.1-fast | veo-3.1-quality | veo-3.1-lite
LIVEFLOW_FLOW_RESOLUTION=720p           # 720p | 1080p
LIVEFLOW_FLOW_DURATION_SECONDS=8        # 4 | 6 | 8
LIVEFLOW_FLOW_CREDITS_TOTAL=1050        # số dư ban đầu, app tự trừ dần
LIVEFLOW_FLOW_AUTO_GENERATE=true        # false: app chỉ dán prompt, bạn tự bấm Tạo
LIVEFLOW_FLOW_AUTO_IMPORT=true          # false: không tự nhận file tải về
LIVEFLOW_FLOW_WAIT_TIMEOUT_SECONDS=420
LIVEFLOW_FLOW_CREDIT_COST=0             # >0 khi Google đổi giá tín dụng
```

Mỗi gói việc nằm trong `%LOCALAPPDATA%\LiveFlowAIStudio\flow_packs\<mã gói>\`:
`manifest.json` (trạng thái), `HUONG_DAN.txt`, `prompts\*.txt` (mở Notepad là copy được),
`anh_tham_chieu\`. Tắt app mở lại vẫn chạy tiếp đúng việc còn lại.

---

## 8. Có sẵn một thư mục video rồi?

Dùng bản dòng lệnh (không cần mở app):

```powershell
python scripts\import_flow_clips.py "C:\Users\Ban\Downloads\flow"            # chỉ kiểm tra
python scripts\import_flow_clips.py "...\flow" --cai-dat --avatar-id 1 --ten mai_phuong
```

Đặt tên file có chứa từ khoá cue (`wave`, `hold`, `detail`, `price`, `tryon`, `cta`,
`nod`, `surprise`, `idle`) để script tự xếp đúng chỗ.

---

Muốn chuyển sang gọi API Veo tự động hoàn toàn (phải bật Billing Google Cloud):
xem **[HUONG_DAN_GOOGLE_PRO.md](HUONG_DAN_GOOGLE_PRO.md)** mục *CÁCH B*.
