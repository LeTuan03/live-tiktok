# 🎬 Hướng dẫn dùng tài khoản Google Pro để tạo Người Mẫu AI cho LiveFlow

Tài liệu dành cho người vận hành LiveFlow **đã có gói Google AI Pro / Google One AI Premium**.

> ⚡ **Đọc cái này trước:** LiveFlow giờ có **chế độ Google Flow làm sẵn mọi thứ trong app**
> (tab 🎞️ Google Flow): tự dán prompt, tự bấm tạo, tự tải và nạp clip, tự đếm tín dụng.
> Không phải làm tay như Cách A dưới đây nữa.
> 👉 **[docs/HUONG_DAN_GOOGLE_FLOW.md](HUONG_DAN_GOOGLE_FLOW.md)**
>
> Tài liệu này giữ lại để: (1) hiểu vì sao gói Pro ≠ quota API, (2) làm tay khi cần,
> (3) hướng dẫn bật Billing nếu muốn gọi API Veo tự động hoàn toàn (Cách B).

---

## ⚠️ 1. Điều cần hiểu trước: gói Pro ≠ quota API

Đây là chỗ gây nhầm lẫn nhiều nhất. Google có **hai hệ thống tính tiền tách rời**:

| | Gói Google AI Pro | API (LiveFlow dùng cái này) |
|---|---|---|
| Dùng ở đâu | App Gemini, Flow, NotebookLM trên web | Gọi bằng API key từ phần mềm |
| Trả tiền kiểu gì | Thuê bao tháng | Trả theo lượng dùng, phải bật Billing |
| Veo tạo video | ✅ Có (bấm tay trong app) | ❌ Chỉ có khi đã bật Billing |

**Trả tiền gói Pro KHÔNG mở quota cho API key.** Nếu LiveFlow báo lỗi:

```
429 Rate limit exceeded ... limit: 0 ... on Free Tier
404 models/veo-3.x... is not found for API version v1beta
```

thì đó là API key đang ở Free Tier, không liên quan gói Pro bạn đang trả.

👉 Vì vậy có **2 cách đi**. Cách A dùng được ngay hôm nay với gói Pro. Cách B tự động hơn nhưng phải bật Billing.

---

## 🅰️ CÁCH A — Dùng gói Pro tạo video thủ công, nạp vào LiveFlow (khuyên dùng trước)

Ý tưởng: dùng **Veo trong app Gemini / Flow** (đã nằm trong gói Pro) để tạo sẵn video người mẫu, rồi nạp file vào LiveFlow. Làm **một lần dùng mãi**, không tốn API mỗi phiên live.

Cách này hợp với kiến trúc LiveFlow: render trước ra MP4 → OBS phát.

### A1. Tạo video người mẫu chính (video loop)

Vào app Gemini (hoặc Flow) → chọn tạo video → dán prompt dưới đây:

```
Vertical 9:16 video, 10 seconds, photorealistic 4k.
A friendly Vietnamese female livestream host in her mid-20s, smart casual outfit,
standing still facing the camera in a medium shot from the knees up.
She smiles warmly, blinks naturally, makes small natural gestures with her hands,
and shifts her weight slightly — as if listening and waiting to speak.
Background: a completely flat, uniform chroma key green screen (pure #00FF00),
evenly lit with no shadows falling on the background.
Static locked-off camera. No zoom, no camera movement, no cuts, no text overlay.
```

**Yêu cầu kỹ thuật bắt buộc** (sai là chroma key sẽ bị rỗ):

- ✅ Khung **dọc 9:16**
- ✅ Nền **xanh lá phẳng #00FF00**, không đổ bóng lên nền
- ✅ **Không zoom, không chuyển cảnh, không chữ** — vì LiveFlow sẽ loop clip này
- ✅ Người mẫu đứng **nguyên trong khung**, không đi ra khỏi mép
- ✅ Nên tạo 2-3 bản rồi chọn bản mượt nhất

> 💡 Tạo thêm 1 **ảnh tĩnh** cùng người mẫu, cùng nền xanh để làm ảnh đại diện (preview).

### A2. Tạo 9 clip cử chỉ

LiveFlow đã định nghĩa sẵn 9 tình huống. Mỗi clip chỉ cần **3-5 giây**.

Dùng chung phần mở đầu prompt ở A1, rồi thay câu mô tả hành động:

| Cue trong app | Câu mô tả hành động thay vào prompt |
|---|---|
| `WAVE_GREET` | `She waves at the camera with a bright welcoming smile.` |
| `HOLD_PRODUCT` | `She holds a product up at chest height, presenting it toward the camera.` |
| `SHOW_DETAIL` | `She points at a detail on the product she is holding, explaining it.` |
| `POINT_PRICE` | `She points toward the upper right corner of the frame with an excited expression.` |
| `TRY_ON` | `She demonstrates wearing or using the product naturally.` |
| `CTA_POINT_DOWN` | `She points down toward the bottom left corner of the frame, urging viewers to tap.` |
| `NOD_LISTEN` | `She nods slowly and smiles while listening attentively to the camera.` |
| `SURPRISE` | `Her eyes widen with delighted surprise, hands raised slightly.` |
| `IDLE_TALK` | `She talks naturally to the camera with relaxed hand gestures.` |

> ⚠️ **Giữ nguyên người mẫu** giữa các clip: dùng cùng một prompt mô tả ngoại hình, hoặc dùng tính năng tham chiếu ảnh/nhân vật nếu app của bạn có. Khác mặt giữa các clip là người xem nhận ra ngay.

### A3. Nạp vào LiveFlow

#### Cách nhanh — để script làm hộ (khuyên dùng)

Tải hết video từ Flow về **một thư mục**, đặt tên file có chứa từ khoá của cue
(`wave`, `hold`, `detail`, `price`, `tryon`, `cta`, `nod`, `surprise`, `idle`).
Rồi chạy:

```powershell
# Bước 1 — chỉ kiểm tra, chưa đụng vào gì
python scripts\import_flow_clips.py "C:\Users\Ban\Downloads\flow"
```

Script in ra bảng: khung hình, độ dài, **% nền xanh**, có tiếng hay không, và cảnh báo
những clip sẽ gây lỗi chroma key — bắt lỗi trước khi bạn mất công nạp vào.

```
FILE                      PHAN LOAI      KHUNG       DAI  NEN XANH  TIENG
clip_wave_greet.mp4       WAVE_GREET     720x1280   4.0s       80%  khong
point_price_ngang.mp4     POINT_PRICE    1280x720   4.0s      100%  khong

--- CANH BAO ---
  [point_price_ngang.mp4] Khung NGANG 1280x720 — can khung DOC 9:16, se phai them vien den
```

Sửa xong các cảnh báo thì chạy lệnh nạp thật:

```powershell
# Bước 2 — chuẩn hoá về 1080x1920@30 và nạp vào LiveFlow
python scripts\import_flow_clips.py "C:\Users\Ban\Downloads\flow" ^
    --cai-dat --avatar-id 1 --ten mai_phuong --video-chinh model_loop.mp4
```

Script sẽ: chuẩn hoá tất cả về **1080x1920@30 h264** (định dạng OBS đọc mượt nhất),
đặt đúng tên file, tạo ảnh đại diện, copy vào `storage/avatars/`, và **đăng ký sẵn
9 cue** vào thư viện clip.

> Clip được đăng ký ở trạng thái **chưa duyệt** — bạn vẫn phải tự xem lại rồi bấm
> **Duyệt** trong app. Script không tự duyệt thay bạn.

- `--avatar-id` lấy ở tab Người Mẫu (bỏ qua thì chỉ copy file, không đăng ký cue)
- `--video-chinh` dùng khi tên file video chính lỡ trùng từ khoá cue (vd `model_idle_loop.mp4`)

#### Cách thủ công — bấm trong app

**Người mẫu chính:**

1. Mở LiveFlow → tab **💃 Quản Lý Người Mẫu AI**
2. Bấm **➕ Thêm Người Mẫu Mới** (hoặc bấm **✏️** trên thẻ người mẫu có sẵn để thay file)
3. Ở ô **Video / Ảnh mẫu:** bấm **📁 Chọn tệp** → chọn file `.mp4` vừa tạo
4. Tích ô **Tách nền xanh (Chroma Key) khi phát trên OBS**
5. Chọn **Giọng đọc mặc định**, **Vị trí trên OBS**, **Kích thước trên OBS**
6. Bấm **💾 Lưu Người Mẫu**

**Clip cử chỉ:**

1. Trên thẻ người mẫu → bấm **🎬 Cử chỉ & Duyệt (9 Cues)**
2. Với từng cue, nhập file `.mp4` tương ứng
3. **Duyệt** clip — LiveFlow chỉ dùng clip đã được duyệt khi lên sóng

**Mẹo đặt tên file để tự nạp:** nếu bạn đặt đúng tên dưới đây và để trong thư mục `storage/avatars/` của dự án, LiveFlow sẽ tự nhận 4 người mẫu mặc định mà không cần thao tác tay:

```
storage/avatars/mai_phuong.mp4   + mai_phuong.jpg
storage/avatars/thu_thao.mp4     + thu_thao.jpg
storage/avatars/minh_khoi.mp4    + minh_khoi.jpg
storage/avatars/hai_dang.mp4     + hai_dang.jpg
```

### A4. Kiểm tra sau khi nạp

- [ ] Thẻ người mẫu hiện ảnh thật, không còn hình bóng xám
- [ ] Bấm **🔴 OBS** trên thẻ người mẫu → nguồn hiện lên, nền xanh đã bị cắt sạch
- [ ] Viền tóc/vai không bị rỗ xanh (nếu rỗ: chỉnh similarity/smoothness của bộ lọc Chroma Key trong OBS)
- [ ] Clip loop không bị giật ở điểm nối

> ℹ️ Khi người mẫu là **video thật**, LiveFlow dùng thẳng video đó — không chạy engine cử động 2D nữa. Engine 2D chỉ dùng khi người mẫu là **ảnh tĩnh**.

---

## 🅱️ CÁCH B — Bật Billing để LiveFlow tự sinh video theo từng sản phẩm

Đây mới là chế độ tự động hoàn toàn: với mỗi sản phẩm, AI tự viết kịch bản 3 phân cảnh rồi tự render video người mẫu cầm đúng sản phẩm đó.

### B1. Bật Billing

1. Vào https://aistudio.google.com → **Get API key**
2. Chọn (hoặc tạo) một project Google Cloud
3. Vào https://ai.dev/rate-limit → bấm nâng tier, liên kết tài khoản thanh toán
4. Chờ vài phút cho tier cập nhật

### B2. Kiểm tra đã lên tier chưa

Chạy lệnh sau trong thư mục dự án (thay `<API_KEY>`):

```powershell
curl "https://generativelanguage.googleapis.com/v1beta/models?key=<API_KEY>"
```

Nếu trong kết quả **đã thấy các model `veo-...`** là đã lên tier thành công. Còn chỉ thấy `gemini-omni-*` thì vẫn đang Free Tier.

### B3. Cấu hình LiveFlow

Mở file `.env` ở thư mục gốc dự án:

```ini
LIVEFLOW_GEMINI_API_KEY=<API key đã bật billing>

# Chọn MỘT trong hai:
LIVEFLOW_VEO_MODEL=gemini-omni-1.1-flash     # người mẫu tự nói tiếng Việt, mồm khớp lời
# LIVEFLOW_VEO_MODEL=veo-3.1-generate-preview  # chỉ dùng khi lệnh ở B2 đã thấy model này

LIVEFLOW_VEO_RESOLUTION=720p
LIVEFLOW_VEO_USE_NATIVE_AUDIO=true
LIVEFLOW_VEO_ALLOW_LOCAL_FALLBACK=false
```

Giải thích 2 khóa quan trọng:

- `LIVEFLOW_VEO_USE_NATIVE_AUDIO=true` → model tự đọc lời tiếng Việt, **mồm khớp lời**. Để `false` thì dùng Edge-TTS ghép vào, mồm sẽ không khớp.
- `LIVEFLOW_VEO_ALLOW_LOCAL_FALLBACK=false` → khi cloud lỗi thì **báo lỗi rõ ràng**. Để `true` sẽ âm thầm tụt về ảnh tĩnh cử động nhẹ — dễ khiến bạn tưởng đang chạy tốt.

### B4. Dùng

Tab **Sản phẩm** → chọn sản phẩm → bấm **✨ Video Veo 3 AI** (hoặc **✨ Tạo Video & Live Tự Động (1-Click)** để chạy luôn cả chuỗi). LiveFlow sẽ:

1. Cho AI viết kịch bản 3 phân cảnh (HOOK → FEATURE_DEMO/USP → CTA) bám đúng dữ liệu sản phẩm
2. Tạo giọng đọc cho từng câu thoại
3. Ghép ảnh người mẫu + ảnh sản phẩm thành khung hình đầu
4. Gửi sang Veo/Omni render video người mẫu cầm đúng sản phẩm đó
5. Ghép tiếng, chèn phụ đề, nối 3 cảnh thành video hoàn chỉnh

### 💰 B5. Lưu ý chi phí (quan trọng)

Veo/Omni tính tiền **theo giây video sinh ra**. Một phiên live 3 tiếng mà gọi liên tục sẽ rất tốn.

**Cách dùng đúng:**

- ✅ Render trước mỗi sản phẩm **một lần**, lưu lại, phát đi phát lại trong nhiều phiên
- ✅ Dựng playlist từ các video đã render sẵn
- ❌ Đừng để hệ thống gọi Veo trong lúc đang live
- ❌ Đừng bật render lại hàng loạt khi chỉ sửa giá — sửa banner giá là đủ

Nên đặt **hạn mức chi tiêu (budget alert)** trong Google Cloud Billing trước khi chạy thật.

---

## 🧭 Nên chọn cách nào?

| Tình huống | Chọn |
|---|---|
| Muốn lên sóng sớm, ngân sách chặt | **Cách A** — 1 người mẫu + 9 clip, dùng mãi |
| Mỗi sản phẩm cần một video review riêng | **Cách B** |
| Vừa muốn tiết kiệm vừa muốn đa dạng | **A trước, B sau** — nền tảng là clip dựng sẵn, chỉ render Veo cho sản phẩm chủ lực |

Khuyến nghị: **làm Cách A trước**. Nó cho bạn người mẫu thật ngay hôm nay bằng gói Pro đang có, và vẫn giữ nguyên đường nâng cấp lên Cách B sau.

---

## 🆘 Xử lý sự cố

| Hiện tượng | Nguyên nhân & cách xử lý |
|---|---|
| Người mẫu vẫn là hình bóng xám | Chưa nạp file, hoặc `storage/avatars/` trống. Xem lại A3. |
| Viền người mẫu rỗ xanh trên OBS | Nền lúc tạo không phẳng. Tạo lại với yêu cầu "flat uniform #00FF00, no shadows", hoặc chỉnh Similarity trong bộ lọc Chroma Key của OBS. |
| Mồm không khớp lời | Clip câm ghép TTS. Đặt `LIVEFLOW_VEO_USE_NATIVE_AUDIO=true` (Cách B), hoặc tạo clip có sẵn tiếng Việt (Cách A). |
| Lỗi `429 ... limit: 0 ... Free Tier` | API key chưa bật Billing. Xem B1. |
| Lỗi `404 ... veo-3.x not found` | Model Veo chưa mở cho key này. Chạy lệnh ở B2 để xem key có model nào, rồi đặt đúng tên vào `LIVEFLOW_VEO_MODEL`. |
| Kịch bản chung chung, không nhắc tính năng sản phẩm | Xem log có dòng `Không đọc được JSON kịch bản Veo` không. Nếu có, nghĩa là AI bị cắt giữa chừng — báo lại để tăng thêm hạn mức token. |
| Mặt người mẫu khác nhau giữa các clip | Prompt mô tả ngoại hình không thống nhất. Dùng đúng một đoạn mô tả cho cả 10 clip. |
