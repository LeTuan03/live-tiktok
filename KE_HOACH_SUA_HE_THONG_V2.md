# KẾ HOẠCH SỬA TOÀN BỘ HỆ THỐNG — LiveFlow v2

> Mục tiêu: biến LiveFlow từ "app demo chạy được trên máy dev" thành **hệ thống LIVE bán hàng chạy thật**,
> với **người mẫu AI trông như người thật, có cử chỉ, cầm/chỉ vào sản phẩm** khi giới thiệu.
>
> Ngày lập: 04/10/2026 · Nhánh hiện tại: `feat/01` · Trạng thái test: 53/53 pass (nhưng test không phủ phần chạy thật)

---

## 0. TL;DR — đọc 1 phút

**Chẩn đoán gốc:** hệ thống hiện tại cố **điều phối thời gian thực** (video + audio + text là 3 nguồn riêng trong OBS,
đồng bộ bằng `time.sleep` và thời lượng *ước lượng*). Kiến trúc này luôn lệch, luôn vỡ khi chạy thật.
Thêm vào đó, "người mẫu AI" hiện tại **không phải người mẫu** — là 1 file PNG vẽ hình tròn trắng trên nền xanh.

**Hướng sửa (xương sống của v2):**

> **RENDER TRƯỚC — PHÁT SAU.**
> Mỗi sản phẩm được render **offline** thành **1 file MP4 dọc 1080×1920 hoàn chỉnh**:
> **bối cảnh** (studio/phòng/quầy) + người mẫu AI đứng trong bối cảnh đó (lipsync + cử chỉ theo cue)
> + sản phẩm + **nhịp cắt cảnh toàn → trung → cận** + banner tên/giá + giọng đọc đã nhúng sẵn.
> Khi LIVE, OBS chỉ việc **phát lần lượt các file MP4 đó**.

**Chuẩn chất lượng:** mỗi video phải trông như **một clip review sản phẩm thật** — có khung cảnh thật
(không phải ảnh sản phẩm dán toàn khung), người mẫu ở trong khung cảnh đó (đúng phối cảnh, đúng ánh sáng,
có bóng đổ), và có cắt cảnh sang cận cảnh sản phẩm khi nói chi tiết. Chi tiết ở **§4.3 (bối cảnh)** và **§4.4 (nhịp cắt cảnh)**.

Lợi ích: hết 100% lỗi lệch tiếng/hình, hết lỗi "Desktop Audio", chạy được trên máy **không có GPU**,
chất lượng xem trước được trước khi lên sóng, và cử chỉ người mẫu khớp đúng câu nói.

**Quyết định cần bạn chốt (ở §4.6)** — chọn nhà cung cấp render người mẫu AI. Khuyến nghị: **Cấp A (cloud pre-render)**.

---

## 1. Vì sao bản hiện tại KHÔNG chạy được thật

Bảng dưới là lỗi **thực tế khi lên sóng**, không phải lỗi code style. Mỗi dòng đều có dẫn chứng.

### 1.1 Nhóm lỗi "lên sóng là thấy ngay" (P0 — nghiêm trọng)

| # | Lỗi | Dẫn chứng | Hậu quả thật trên LIVE |
|---|---|---|---|
| 1 | **Giọng AI đọc to cả ghi chú cử chỉ.** Prompt yêu cầu LLM chèn `(giơ sản phẩm lại gần camera)`, `(cười tươi)` vào kịch bản, nhưng TTS nhận **nguyên văn** `script.content`. | `app/services/script_service.py:109` (yêu cầu chèn cue) + `app/ui/pages/voice_page.py:105` (`generate_speech(s.id, s.content, ...)` — không lọc) | Khách nghe: *"…chị ơi, giơ sản phẩm lại gần camera, cười tươi, giá chỉ còn…"*. **Mất uy tín ngay lập tức.** |
| 2 | **Người mẫu AI là hình vẽ placeholder.** 4 "người mẫu" mặc định được tạo bằng Pillow: 1 hình chữ nhật bo góc + 1 hình tròn trắng + 1 nửa ellipse, trên nền xanh 400×600. | `app/services/avatar_service.py:_generate_avatar_art` (vẽ `rounded_rectangle` / `ellipse` / `chord`) | Lên OBS, chroma key chỉ xóa nền xanh → **còn lại 1 khối màu tím có vòng tròn trắng**, scale lên 410×950 từ ảnh 400×600 → mờ nhòe. Không thể lên sóng. |
| 3 | **Âm thanh mặc định phát qua loa máy tính**, trông chờ OBS thu "Desktop Audio". | `app/ui/pages/live_studio_page.py:263` — item đầu tiên (mặc định) là `"Máy tính này (OBS thu 'Desktop Audio')"` | LIVE **im lặng** nếu OBS không bật Desktop Audio / sai device / cắm tai nghe; hoặc **thu luôn tiếng thông báo Windows, tiếng Zalo, tiếng video khác**. |
| 4 | **Trả lời bình luận xong là mất luôn giọng đọc sản phẩm.** Ở chế độ `local`, `_pause_media()` gọi `stop_audio_requested` (dừng hẳn), còn `_resume_media()` **không làm gì** ở chế độ local. | `scheduler_service.py:431-445` | Đọc 1 câu trả lời khách → phần còn lại của bài giới thiệu sản phẩm **không bao giờ phát tiếp**, chỉ còn đồng hồ chạy trong im lặng. |
| 5 | **Hệ thống luôn tưởng là đã phát được audio.** `_play_audio()` `return True` vô điều kiện, kể cả khi OBS từ chối file. | `scheduler_service.py:404-415` | Thanh tiến trình chạy, log ghi "🔊 Đang đọc kịch bản" — nhưng **trên sóng không có tiếng**. Vi phạm đúng nguyên tắc "không báo thành công khi chưa thành công" mà chính dự án đặt ra. |
| 6 | **Thời lượng là số ước lượng, không phải số thật.** Khi không có ffprobe, thời lượng = `filesize / 6000`. | `voice_service.py:_EDGE_TTS_BYTES_PER_SECOND = 6000` + `estimate_mp3_duration` | Giọng bị **cắt giữa câu**, hoặc **im lặng 10-20s** cuối mỗi sản phẩm. Cơ chế `notify_local_audio_finished()` có viết nhưng `_wait_item()` **không hề chờ** event này (`_local_audio_done` bị `clear()` rồi bỏ quên). |
| 7 | **Trích xuất sản phẩm được phép bịa số liệu.** Prompt kết thúc bằng: *"Nếu thông tin bị thiếu do trang web bảo mật, hãy dùng trí tuệ nhân tạo suy luận thông minh … điền đầy đủ các thông số thực tế."* | `product_extractor_service.py` (câu cuối của `prompt`) | Shopee/TikTok Shop **chặn** `aiohttp` (trả JS shell hoặc 403) → gần như **mọi lần dán link đều cho ra giá và thông số do AI tự nghĩ ra**, rồi **đọc to trên sóng**. Rủi ro pháp lý + khách chốt đơn sai giá. |

### 1.2 Nhóm lỗi kiến trúc (P1 — làm hệ thống không thể tin được)

| # | Lỗi | Dẫn chứng | Hậu quả |
|---|---|---|---|
| 8 | **Video và audio là 2 nguồn OBS độc lập.** Video `looping=True` chạy vòng vô tận, audio phát 1 lần, không có cơ chế khớp. | `scheduler_service.py:_present()` — `set_media_source_file(video, loop=True)` rồi `_play_audio(audio)` riêng | Miệng/cử chỉ người mẫu **không bao giờ khớp lời**. Đây chính là lý do không thể làm "người mẫu có cử chỉ" trên kiến trúc hiện tại. |
| 9 | **Tạm dừng không dừng hình.** `pause()` chỉ pause audio; video sản phẩm vẫn loop. | `scheduler_service.py:431` | Bấm "Tạm dừng" → khách vẫn thấy video chạy nhưng **không có tiếng**, như bị lỗi mạng. |
| 10 | **Không ép canvas 9:16.** Chỉ có 1 dòng *gợi ý* trong hộp thoại: "đặt canvas OBS 1080×1920". | `live_studio_page.py:_setup_obs_scene` (chuỗi "Gợi ý: đặt canvas…") | Người dùng để mặc định 1920×1080 → TikTok nhận khung ngang, **sản phẩm bị cắt 2 bên hoặc viền đen khổng lồ**. OBS WebSocket có `SetVideoSettings` nhưng không dùng. |
| 11 | **Thứ tự lớp (z-order) không xác định.** Comment trong code tự nhận "OBS puts newer items on top", nhưng nguồn đã tồn tại được `_ensure_in_scene` thêm lại → nằm trên cùng. | `obs/client.py:ensure_liveflow_setup` + `_ensure_in_scene` | Chạy "Tạo scene" lần 2 → **banner tên/giá bị video che mất**, hoặc người mẫu nằm dưới ảnh sản phẩm. |
| 12 | **Banner tên + giá chỉ là 1 text source trần**, không nền, không khung, đặt ở 5%/78%. | `obs/client.py:_place_text_bottom` | Chữ trắng trên ảnh sáng → **không đọc được**. Không phải "banner 9:16 kiểu demo" như yêu cầu. |
| 13 | **D-ID gọi API thiếu tham số bắt buộc.** Biến `source_url` được tính rồi **không đưa vào payload**. | `avatar_service.py:_generate_did_video` (`source_url = ...` nhưng `payload` không có khóa này) | API trả 400 → "Người mẫu AI" cấp cao **chưa từng chạy được lần nào**. Kể cả nếu thêm, D-ID cần URL công khai, không nhận đường dẫn file local. |
| 14 | **HeyGen chỉ gửi đi rồi bỏ.** Trả về `(None, "HeyGen video submitted (ID: ...)")` — không poll, không tải về. | `avatar_service.py:_generate_heygen_video` | Người dùng trả tiền API, nhận được 1 dòng thông báo, **không có video**. Thêm nữa `avatar_id: "default"` không phải ID hợp lệ. |
| 15 | **Đọc bình luận = dán tay.** Toàn bộ `READ_COMMENTS` / `SEND_COMMENT` là `MANUAL`. | `integrations/tiktok/capabilities.py` | Nghĩa là **phải có người ngồi canh suốt phiên**, copy từng bình luận. Đây không phải "auto-live" — mâu thuẫn với chính tên tính năng "AI Auto-Live". |

### 1.3 Nhóm lỗi vận hành / triển khai (P2)

| # | Lỗi | Hậu quả |
|---|---|---|
| 16 | Không có **preflight** (kiểm tra trước khi lên sóng). Hộp thoại cảnh báo hiện tại chỉ đếm "thiếu audio/video" rồi cho bấm "Vẫn bắt đầu". | Lên sóng rồi mới biết thiếu nguồn OBS, sai canvas, chưa có stream key, hết dung lượng ổ. |
| 17 | `max_consecutive_errors = 3` → **tắt điều phối giữa phiên LIVE** khi 3 sản phẩm lỗi liên tiếp. | Phiên LIVE đang có người xem thì **màn hình đứng im**, không có chế độ "phát video dự phòng / filler". |
| 18 | Không có **nguồn phát dự phòng (filler)**. Khi 1 mục lỗi → không hiển thị gì. | Khách thấy màn hình đen → thoát. TikTok tụt reach. |
| 19 | `app/workers/` rỗng; mọi việc nặng (TTS, render, gọi LLM) chạy trong UI thread hoặc QThread rời rạc; `product_showcase_dialog._on_send_to_obs` tạo `asyncio.new_event_loop()` và `run_until_complete` **ngay trong UI thread**. | **App treo (Not Responding)** nhiều giây khi tổng hợp giọng — giữa lúc đang LIVE. |
| 20 | Chưa có đóng gói/giám sát cho máy triển khai: `BUILD_EXE.bat` có nhưng không có kiểm tra môi trường đích (OBS chưa cài, GPU, ffmpeg, phông chữ tiếng Việt). | Mang sang máy khác là vỡ. |

---

## 2. Mô hình vận hành đúng thực tế (v2 phải làm được)

Một phiên LIVE bán hàng thật, chạy 2-6 tiếng, cần đúng 6 việc sau:

```
[1] CHUẨN BỊ (offline, trước khi LIVE)
    Sản phẩm (ảnh toàn + ảnh CẬN + giá đã xác minh)  ⊕  BỐI CẢNH (set đã cấu hình)
      → Kịch bản phân đoạn (text nói ⊕ cue cử chỉ ⊕ shot góc máy)
      → TTS từng đoạn (thời lượng THẬT bằng ffprobe)
      → Người mẫu AI render clip lipsync + cử chỉ theo từng cue
      → Ghép (ffmpeg) theo timeline góc máy:
          bối cảnh + người mẫu (có bóng đổ, grade màu) + cận sản phẩm + banner 9:16
      → MỘT FILE: product_<id>_v<n>.mp4  (1080×1920, audio nhúng sẵn)
      → DUYỆT TAY (xem thử, đạt mới cho vào playlist)

[2] LÊN SÓNG
    OBS canvas 1080×1920 ← scene "LiveFlow" ← 1 media source duy nhất
    phát lần lượt các MP4 đã duyệt  (vòng lặp liên tục)

[3] TƯƠNG TÁC
    Bình luận → gợi ý trả lời có căn cứ → duyệt → chèn "clip trả lời" (3-15s)
    vào giữa 2 sản phẩm, hoặc overlay chữ + giọng nói

[4] AN TOÀN KHI LIVE
    Mục lỗi → tự chuyển mục kế tiếp, KHÔNG dừng phiên
    Hết mục / lỗi nặng → phát FILLER (clip chờ đã render sẵn)
    Mất kết nối OBS → tự kết nối lại, ghi nhật ký, cảnh báo

[5] GIÁM SÁT
    Đang phát mục nào · còn bao lâu · bitrate/dropped frames từ OBS
    · số bình luận đã trả lời · cảnh báo đỏ

[6] SAU PHIÊN
    Báo cáo: sản phẩm đã giới thiệu, số vòng, lỗi, bình luận chưa trả lời
```

**Nguyên tắc bất biến của v2:**

1. **Không bao giờ đồng bộ tiếng/hình lúc đang LIVE.** Mọi thứ cần khớp nhau phải được **nung chảy vào 1 file MP4** từ trước.
2. **Không báo thành công nếu OBS/Edge/API chưa xác nhận.** Mọi hàm trả `bool` phải kiểm chứng lại bằng truy vấn trạng thái.
3. **Không bao giờ để AI tự điền số liệu sản phẩm.** Thiếu dữ liệu = ô trống + nhãn "chưa xác minh", không được đọc trên sóng.
4. **Không bao giờ để màn hình trống.** Luôn có filler.
5. **Cue cử chỉ không bao giờ đi vào TTS.** Hai luồng dữ liệu tách biệt hoàn toàn.

---

## 3. Kiến trúc v2

### 3.1 Tổng quan module

```
app/
├─ domain/                        ★ MỚI — kiểu dữ liệu thuần, không phụ thuộc Qt/DB
│   ├─ segment.py                  Segment(text, cue, shot, emphasis, duration)
│   ├─ cue.py                      Cue enum (cử chỉ) + Shot enum (góc máy)
│   └─ render_job.py               RenderJob, RenderStatus, RenderArtifact
│
├─ presenter/                     ★ MỚI — "người mẫu AI" (trái tim của v2)
│   ├─ base.py                     PresenterProvider (giao diện chung)
│   ├─ clip_library.py             Thư viện clip cử chỉ của từng người mẫu
│   ├─ providers/
│   │   ├─ heygen_provider.py      render clip nói + cử chỉ (cloud)
│   │   ├─ did_provider.py         (sửa lại cho đúng API)
│   │   ├─ sync_lipsync.py         lipsync clip cử chỉ sẵn có với audio TTS
│   │   ├─ i2v_provider.py         ảnh sản phẩm + người mẫu → clip cầm sản phẩm
│   │   └─ local_loop.py           cấp thấp nhất: ghép clip loop + audio (ffmpeg)
│   └─ gesture_planner.py          Kịch bản → chuỗi (cue, thời điểm, thời lượng)
│
├─ sets/                          ★ MỚI — BỐI CẢNH (khung cảnh review)
│   ├─ scene_set.py                SceneSet: nền, đường sàn, điểm đứng, ánh sáng, blur
│   ├─ library.py                  thư viện bối cảnh (studio, phòng khách, quầy, bếp…)
│   └─ matcher.py                  khớp người mẫu vào bối cảnh: tỉ lệ, màu, bóng đổ
│
├─ render/                        ★ MỚI — xưởng render offline
│   ├─ queue.py                    hàng đợi bền (SQLite), retry, huỷ, ưu tiên
│   ├─ worker.py                   tiến trình riêng (KHÔNG chạy trong UI thread)
│   ├─ shot_planner.py             segments → timeline góc máy (toàn/trung/cận/split)
│   ├─ compositor.py               ffmpeg: bối cảnh ⊕ người mẫu ⊕ cận SP ⊕ banner → MP4
│   └─ banner.py                   vẽ banner 9:16 bằng Pillow (tên, giá, CTA, khung)
│
├─ playout/                       ★ MỚI — thay thế scheduler_service.py cũ
│   ├─ engine.py                   phát danh sách MP4, vòng lặp, chèn, filler
│   ├─ preflight.py                12 mục kiểm tra chặn-cứng trước khi LIVE
│   └─ obs_compositor.py           dựng/kiểm chứng scene 9:16, z-order, canvas
│
├─ comments/                      ★ MỚI
│   ├─ sources/screen_ocr.py       đọc panel chat LIVE Studio trên MÁY CỦA BẠN
│   ├─ sources/manual.py           dán tay (giữ lại, làm fallback)
│   └─ reply_clips.py              ngân hàng clip trả lời render sẵn
│
└─ (giữ, sửa) services/ integrations/ ui/ database/
```

### 3.2 Tách lời nói ↔ cử chỉ (sửa lỗi #1 và mở đường cho người mẫu)

Kịch bản **không còn là một khối text**. LLM phải trả JSON phân đoạn:

```json
{
  "set_id": "quay_my_pham_studio",
  "segments": [
    { "text": "Chào cả nhà, hôm nay shop mình có em kem dưỡng siêu hot đây ạ!",
      "cue": "WAVE_GREET",     "shot": "WIDE_MODEL",      "emphasis": "high" },
    { "text": "Các chị xem kỹ hộp này nha, dung tích 50ml, chất kem mịn.",
      "cue": "HOLD_PRODUCT",   "shot": "MEDIUM_MODEL",    "emphasis": "normal" },
    { "text": "Đây là phần bảng thành phần, các chị đọc cùng em.",
      "cue": "SHOW_DETAIL",    "shot": "CLOSEUP_PRODUCT", "emphasis": "normal" },
    { "text": "Giá gốc ba trăm năm mươi nghìn, hôm nay trong live còn một trăm chín.",
      "cue": "POINT_PRICE",    "shot": "SPLIT",           "emphasis": "high" },
    { "text": "Chị em bấm giỏ hàng góc trái màn hình giúp em nhé!",
      "cue": "CTA_POINT_DOWN", "shot": "MEDIUM_MODEL",    "emphasis": "high" }
  ]
}
```

Hai trục độc lập: **`cue` = người mẫu làm gì** · **`shot` = camera đang ở đâu**. Chính sự thay đổi `shot`
làm video "giống review thật" thay vì một khung tĩnh suốt 2 phút.

**Bộ cue chuẩn (v2.0):**

| Cue | Ý nghĩa | Clip/animation cần có |
|---|---|---|
| `IDLE_TALK` | nói bình thường, tay nhẹ | 8-12s loop |
| `WAVE_GREET` | chào, vẫy tay | 3-5s |
| `HOLD_PRODUCT` | **giơ sản phẩm lên trước ngực** | 6-10s |
| `SHOW_DETAIL` | đưa sản phẩm lại gần camera, chỉ vào chi tiết | 6-10s |
| `POINT_PRICE` | chỉ sang banner giá | 3-5s |
| `TRY_ON` | mặc/đeo/thoa thử sản phẩm | 8-15s |
| `CTA_POINT_DOWN` | chỉ xuống giỏ hàng | 3-5s |
| `NOD_LISTEN` | lắng nghe, gật đầu (dùng khi trả lời bình luận) | 6-10s loop |
| `SURPRISE` | nhấn mạnh deal sốc | 3-5s |

**Bộ góc máy chuẩn (`shot`) — thứ tạo cảm giác "review thật":**

| Shot | Khung hình | Dùng khi |
|---|---|---|
| `WIDE_MODEL` | Toàn thân người mẫu trong bối cảnh, thấy rõ cả set | Mở đầu, chào, chuyển sản phẩm |
| `MEDIUM_MODEL` | Nửa người, sản phẩm trong tay chiếm ~25% khung | Giới thiệu chính, CTA |
| `CLOSEUP_PRODUCT` | **Cận cảnh sản phẩm toàn khung** (ảnh/video shop tự quay), giọng người mẫu vẫn nói | Nói thành phần, chất liệu, chi tiết, nhãn |
| `SPLIT` | Người mẫu 60% dưới + bảng giá/thông số 40% trên | Nói giá, so sánh, deal |
| `INSET_PRODUCT` | Cảnh người mẫu + ô cận sản phẩm nhỏ góc trên | Vừa nói vừa cho xem chi tiết |
| `TRYON_WIDE` | Toàn thân, người mẫu đang mặc/đeo sản phẩm | Thời trang, phụ kiện |

Nhịp đề nghị (có thể cấu hình): **đổi shot mỗi 8-15 giây**, không để 1 shot quá 20s,
không cắt 2 lần trong cùng 1 câu.

Quy tắc code (bắt buộc, có test):
- `Segment.text` → **chỉ** đi vào TTS.
- `Segment.cue` → **chỉ** đi vào presenter (chọn clip cử chỉ).
- `Segment.shot` → **chỉ** đi vào compositor (chọn khung hình/lớp).
- Thêm hàm `strip_stage_directions(text)` lọc mọi `(...)`, `[...]`, `*...*` làm **lưới an toàn**, chạy ngay trước mọi lời gọi TTS, kèm unit test với đúng các ví dụ ở lỗi #1.

### 3.3 Xưởng render (fix lỗi #8, #19)

```
RenderJob(product_id, avatar_id, set_id, voice_id, segments[])
  │
  ├─ bước 1  TTS từng segment      → seg_01.mp3 … seg_n.mp3  (+ ffprobe → thời lượng THẬT)
  ├─ bước 2  gesture_planner       → timeline cử chỉ  [(cue,  t_start, t_dur)]
  ├─ bước 3  shot_planner          → timeline góc máy [(shot, t_start, t_dur)]
  ├─ bước 4  presenter.render()    → clip người mẫu cho từng segment (lipsync + cue)
  ├─ bước 5  sets.matcher          → khớp người mẫu vào bối cảnh (tỉ lệ, màu, bóng đổ)
  ├─ bước 6  compositor            → ffmpeg ghép theo timeline shot:
  │                                   bối cảnh ⊕ người mẫu ⊕ cận sản phẩm ⊕ banner ⊕ audio
  └─ bước 7  verify                → ffprobe: 1080×1920, có stream audio, |lệch a/v| < 100ms,
                                      số lần đổi shot ≥ 3 → fail thì KHÔNG đưa vào playlist
```

Chạy trong **tiến trình riêng** (`multiprocessing`), giao tiếp bằng hàng đợi SQLite →
UI không bao giờ treo, và render vẫn tiếp tục khi đang LIVE (hoặc tạm ngưng để nhường CPU cho encode).

### 3.4 OBS chuẩn hoá (fix #10, #11, #12, #3)

`playout/obs_compositor.py` phải **ép** và **kiểm chứng lại** (không chỉ gợi ý):

| Việc | Cách làm | Kiểm chứng |
|---|---|---|
| Canvas 1080×1920, 30fps | `SetVideoSettings` qua WebSocket | `GetVideoSettings` đọc lại, khác → báo đỏ |
| Scene `LiveFlow Live` | tạo nếu chưa có | `GetSceneList` |
| **1** media source `LiveFlow Player` | nguồn duy nhất phát MP4 đã render | `GetInputSettings` |
| Lớp banner (tuỳ chọn) | `image_source` đọc PNG do `render/banner.py` vẽ | `GetSceneItemList` + so z-order theo chỉ số |
| Z-order xác định | đặt lại bằng `SetSceneItemIndex` cho **từng** item, theo bảng cố định | đọc lại `sceneItemIndex` |
| Audio | **đi theo MP4** (media source) — bỏ hẳn chế độ `local` | `GetInputMute` + `GetInputVolume` ≠ muted |
| Dropped frames | `GetStreamStatus` mỗi 2s | > 2% trong 30s → cảnh báo đỏ trên UI |

> Chế độ "Máy tính này (Desktop Audio)" **bị xoá khỏi UI**, không còn là lựa chọn.

### 3.5 Playout engine (viết lại `scheduler_service.py`)

Đơn giản hoá tối đa — vì mọi thứ khó đã được xử lý ở bước render:

```python
for mp4 in playlist:                      # mỗi mp4 = 1 sản phẩm đã render & duyệt
    obs.set_media_file(PLAYER, mp4)
    wait_until_media_ended(PLAYER)        # ĐỌC TRẠNG THÁI THẬT từ OBS:
                                          # GetMediaInputStatus → cursor/duration
                                          # (không còn time.sleep + ước lượng)
    drain_reply_queue()                   # chèn clip trả lời bình luận nếu có
```

- `wait_until_media_ended` dùng `GetMediaInputStatus` (`media_cursor`, `media_duration`, `media_state`),
  timeout cứng = `duration + 15s` để không treo vĩnh viễn.
- Lỗi 1 mục → **ghi log, sang mục kế tiếp**. Lỗi liên tiếp → **phát filler**, không dừng phiên (fix #17, #18).
- `pause()` → `MEDIA_PAUSE` trên đúng 1 nguồn → **đứng cả hình lẫn tiếng** (fix #9).

### 3.6 Vòng bình luận (fix #15)

3 nguồn, bật/tắt độc lập, xếp theo mức độ tự động:

| Nguồn | Cách hoạt động | Hợp lệ? | Ghi chú |
|---|---|---|---|
| **A. OCR màn hình** (khuyến nghị) | Chọn 1 vùng chữ nhật trên **màn hình máy bạn** (panel chat của TikTok LIVE Studio) → chụp 1-2 lần/giây → OCR (PaddleOCR/Tesseract tiếng Việt) → chống trùng bằng hash dòng | Có — chỉ đọc nội dung đang hiện trên màn hình của chính bạn, không gọi API riêng, không đăng nhập hộ | Cần căn vùng 1 lần. Độ chính xác ~85-95% với font chat. |
| **B. Dán tay** | Giữ nguyên như hiện tại | Có | Fallback khi OCR lệch. |
| **C. API chính thức** | TikTok Shop / Shopee Open Platform | Cần được duyệt đối tác | Ghi vào roadmap dài hạn, **không hứa** trong v2. |

Trả lời: phân loại câu hỏi → tra dữ liệu sản phẩm **đã xác minh** + chính sách shop → gợi ý câu trả lời →
**người duyệt** (hoặc tự động với các câu thuộc danh sách trắng: giá, size, phí ship, bảo hành) →
chọn clip `NOD_LISTEN` + TTS → chèn vào playout.

### 3.7 Dữ liệu sản phẩm phải sạch (fix #7)

- **Xoá** câu cho phép AI suy luận số liệu. Thay bằng: *"Chỉ trích xuất thông tin có trong nội dung được cung cấp. Trường nào không có → trả `null`. Không được suy đoán giá, thành phần, chứng nhận."*
- Mỗi trường có cờ `verified: bool` + `source: url|manual|ai_extracted`.
- **Giá chưa `verified` → không được đưa vào banner và không được đọc trên sóng** (chặn cứng ở preflight).
- Shopee/TikTok chặn fetch → hiện rõ: *"Không đọc được trang. Vui lòng nhập tay hoặc dán nội dung trang."* +
  ô "Dán nội dung trang sản phẩm" để người dùng tự copy (hợp lệ, chính xác, không bịa).

---

## 4. ★ NGƯỜI MẪU AI CÓ CỬ CHỈ VỚI SẢN PHẨM

Đây là phần bạn quan tâm nhất. Nói thẳng về mức khả thi hiện nay:

### 4.1 Sự thật kỹ thuật cần biết trước

| Điều bạn muốn | Khả thi? | Cách làm thực tế |
|---|---|---|
| Người mẫu trông như người thật | ✅ Được | Cloud avatar (HeyGen/D-ID…) hoặc video i2v (Kling/Veo/Hailuo…) |
| Miệng khớp lời nói tiếng Việt | ✅ Được | Lipsync engine; tiếng Việt khớp tốt vì chỉ dựa trên âm thanh |
| Có cử chỉ: vẫy tay, chỉ tay, gật đầu | ✅ Được | Thư viện clip theo cue (§3.2) — **không** phải sinh realtime |
| **Cầm đúng sản phẩm của shop trong tay** | ⚠️ Được nhưng phải duyệt tay | Ảnh sản phẩm + ảnh người mẫu → image-to-video. Tỉ lệ ra clip dùng được ~50-70% → **render 3 bản, chọn 1** |
| Mặc thử đúng bộ quần áo của shop | ⚠️ Được với quần áo | Virtual try-on rồi mới i2v. Trang sức/mỹ phẩm/đồ nhỏ khó hơn |
| Người mẫu **ứng biến realtime** theo bình luận | ❌ Chưa nên làm ở v2 | Cần avatar streaming realtime + GPU mạnh + độ trễ 1-3s. Để Phase 7 |
| Tay thao tác chi tiết (mở nắp, bấm nút, rót nước) | ❌ Không ổn định | AI sinh video vẫn lỗi tay/ngón. **Dùng video thật của shop** cho các cảnh này, người mẫu chỉ thuyết minh |

**Kết luận thiết kế:** người mẫu AI của v2 = **thư viện clip cử chỉ render sẵn + lipsync theo từng đoạn kịch bản**.
Đây là cách các phiên "AI live" đang chạy thật trên TikTok Việt Nam, và là cách duy nhất chạy được trên máy không GPU.

### 4.2 Quy trình tạo 1 người mẫu AI (làm 1 lần, dùng cho hàng trăm phiên)

```
Bước 1 — Tạo nhân dạng (identity)
  • Chọn 1 trong 3:
      (a) Ảnh người thật có hợp đồng/được phép (tốt nhất: chính bạn hoặc mẫu đã ký)
      (b) Avatar có sẵn trong HeyGen/D-ID (đã được cấp quyền thương mại)
      (c) Ảnh người mẫu do AI sinh (Flux/SDXL/Midjourney) — nhất quán bằng cách
          cố định seed + prompt + 1 ảnh tham chiếu
  → Lưu: 1 ảnh chân dung 3/4 người, nền xanh hoặc nền studio, 1080×1920

Bước 2 — Render THƯ VIỆN CLIP CỬ CHỈ (9 cue ở §3.2)
  • Mỗi cue: 1 clip 3-15s, 1080×1920, nền xanh (để chroma key) hoặc nền studio
  • Dùng image-to-video với mô tả cử chỉ cụ thể, VD:
      "người mẫu nữ giơ hộp kem màu hồng lên ngang ngực bằng hai tay,
       mỉm cười nhìn camera, nền xanh lá đồng nhất, camera tĩnh, 9:16"
  • Render 3 biến thể / cue → DUYỆT TAY → giữ bản tốt nhất
  → Kết quả: 9-15 file MP4 trong storage/avatars/<avatar_id>/clips/

Bước 3 — Clip CÓ SẢN PHẨM (làm riêng cho từng sản phẩm quan trọng)
  • Đầu vào: ảnh sản phẩm đã tách nền + ảnh người mẫu
  • i2v với tham chiếu 2 ảnh → clip "giơ sản phẩm", "chỉ vào nhãn", "mặc thử"
  • DUYỆT TAY bắt buộc (kiểm tra: đúng sản phẩm? tay có bị dị dạng? nhãn có bị méo chữ?)
  → storage/products/<id>/presenter_clips/

Bước 4 — Lipsync theo kịch bản (mỗi lần đổi kịch bản/giá)
  • audio TTS của segment + clip của cue tương ứng → lipsync → clip nói
  • Nếu clip ngắn hơn audio: ffmpeg loop/ping-pong clip cho đủ thời lượng
  • Nếu dài hơn: cắt theo audio

Bước 5 — Ghép khung 9:16 cuối cùng theo timeline shot (ffmpeg, local, CPU)
  Xem chi tiết các lớp và 3 kiểu khung hình ở §4.3
  → product_<id>_v<n>.mp4  (có audio)  → DUYỆT → vào playlist
```

### 4.3 ★ BỐI CẢNH (khung cảnh review thật)

Đây là thứ phân biệt "video AI nhìn là biết AI" với "clip review thật". Ảnh sản phẩm dán toàn khung + người mẫu
cắt dán ở góc = **trông như ảnh ghép**. Phải có **set (bối cảnh)** và người mẫu phải **ở trong** set đó.

#### 4.3.1 Thư viện bối cảnh (`sets/library.py`)

Mỗi bối cảnh là một bản ghi có đủ thông số để ghép người mẫu vào đúng:

| Trường | Ý nghĩa | Ví dụ |
|---|---|---|
| `background` | video loop 10-20s hoặc ảnh 1080×1920 | `set_studio_myph.mp4` |
| `floor_y` | toạ độ Y đường sàn (người mẫu đặt chân vào đây) | `1520` |
| `anchor_x` | tâm X chỗ người mẫu đứng | `620` |
| `model_height_px` | chiều cao người mẫu trong khung (quyết định phối cảnh) | `1180` |
| `product_zone` | vùng bàn/kệ để đặt ảnh sản phẩm 3D | `(120, 1240, 480, 1480)` |
| `light_temp` / `light_dir` | nhiệt màu & hướng sáng của set | `5600K`, `trái-trên` |
| `bg_blur` | độ mờ nền (tạo chiều sâu) | `2.5px` |
| `shadow` | bóng đổ dưới chân: độ mờ, độ lệch, độ đậm | `blur 24, dy 8, alpha .35` |
| `grain` | hạt nhiễu phủ chung cả khung | `0.02` |

**Bộ bối cảnh khởi điểm (làm 6 cái là đủ cho hầu hết ngành hàng):**

| Bối cảnh | Dùng cho |
|---|---|
| `studio_den_neon` | thời trang, phụ kiện, đồ công nghệ — nền tối, đèn viền, sang |
| `quay_my_pham_trang` | mỹ phẩm, skincare — quầy trắng, kệ gương, hoa tươi |
| `phong_khach_am` | gia dụng, đồ decor — sofa, cây xanh, ánh sáng ấm |
| `bep_sang` | đồ bếp, thực phẩm — bàn đảo, nắng cửa sổ |
| `shop_thoi_trang` | quần áo — giá treo, gương toàn thân |
| `studio_gradient` | mọi thứ, dự phòng — nền gradient + bàn trưng bày |

Nguồn bối cảnh (theo thứ tự ưu tiên):
1. **Quay thật** tại chỗ bạn bán (chân thật nhất, 0đ, quay 1 lần 15s/góc rồi dùng mãi).
2. **AI sinh ảnh** bối cảnh 9:16 (không có người) → thêm chuyển động rất nhẹ (zoom 1.00→1.04 trong 20s, ping-pong) → thành video loop.
3. Stock video có giấy phép thương mại.

#### 4.3.2 Các lớp của khung hình

```
                 1080 × 1920
  ┌─────────────────────────────────────┐
  │ [L6] Banner trên: tên shop · ĐANG LIVE · nhãn AI (nếu bật)
  │ [L5] Ô cận sản phẩm (chỉ ở shot INSET_PRODUCT)
  │                                     │
  │ [L0] BỐI CẢNH — video loop, hơi mờ (bg_blur) ←── lớp dưới cùng
  │ [L1] Sản phẩm trên bàn/kệ (đặt vào product_zone, có bóng đổ)
  │ [L2] BÓNG ĐỔ của người mẫu (ellipse mờ ở floor_y)
  │ [L3] NGƯỜI MẪU — chroma key, chân đặt đúng floor_y,
  │      cao đúng model_height_px, đã color-grade theo light_temp
  │ [L4] Banner dưới: TÊN SẢN PHẨM · GIÁ GỐC (gạch) · GIÁ LIVE (lớn) · CTA
  │ [L7] Grain phủ toàn khung (hoà hai nguồn ảnh thành một)
  └─────────────────────────────────────┘
```

#### 4.3.3 5 quy tắc "chống nhìn-là-biết-ghép" (bắt buộc code trong `sets/matcher.py`)

1. **Khớp phối cảnh** — chân người mẫu phải đặt đúng `floor_y`, chiều cao đúng `model_height_px`.
   Không được scale tuỳ ý. (Lỗi phổ biến nhất: người mẫu "bay" lơ lửng hoặc to bất thường.)
2. **Khớp màu/ánh sáng** — `colorbalance` + `eq` theo `light_temp` của set. Người mẫu quay nền xanh ánh sáng trắng
   đặt vào phòng khách ánh sáng ấm mà không grade → lộ ngay.
3. **Có bóng đổ** — ellipse mờ dưới chân (L2). Thiếu bóng là dấu hiệu "dán hình" rõ nhất.
4. **Chiều sâu trường ảnh** — làm mờ nền (`bg_blur` 2-4px), giữ người mẫu nét. Mắt người đọc đây là "có ống kính thật".
5. **Hoà hạt nhiễu** — phủ `noise` nhẹ lên **toàn khung sau khi ghép**, để 2 nguồn ảnh khác nhau trở thành một.

Ngoài ra: **viền chroma key** phải được xử lý (despill + co viền 1-2px), nếu không sẽ thấy rìa xanh quanh tóc.

#### 4.3.4 Hai chế độ dựng bối cảnh — chọn theo sản phẩm

| | **Chế độ 1 — GHÉP LỚP** (mặc định) | **Chế độ 2 — BỐI CẢNH NUNG** (sản phẩm chủ lực) |
|---|---|---|
| Cách làm | Người mẫu nền xanh + bối cảnh riêng, ghép bằng ffmpeg | AI sinh **1 clip duy nhất**: người mẫu + sản phẩm + bối cảnh trong cùng khung |
| Độ chân thật | Tốt (nếu làm đủ 5 quy tắc trên) | **Cao nhất** — ánh sáng/bóng/phản chiếu tự nhiên sẵn |
| Linh hoạt | **Cao** — đổi bối cảnh, đổi sản phẩm, đổi giá không cần render lại người mẫu | Thấp — đổi gì cũng phải render lại |
| Chi phí | Thấp (render người mẫu 1 lần dùng mọi sản phẩm) | Cao (mỗi sản phẩm × mỗi bối cảnh = 1 lần render) |
| Rủi ro | Lộ ghép nếu làm cẩu thả | Tay/nhãn sản phẩm bị lỗi → phải render lại |
| Dùng cho | Toàn bộ danh mục | 5-10 sản phẩm bán chạy nhất |

→ v2 code **cả hai**, cùng một giao diện `PresenterProvider`; mỗi sản phẩm chọn chế độ trong trang Sản phẩm.

### 4.4 Nhịp cắt cảnh kiểu review (`render/shot_planner.py`)

Người xem TikTok thoát sau 2-3 giây nếu khung hình bất động. Nhịp cắt là kỹ thuật giữ người, và nó **miễn phí**
(chỉ là ffmpeg ghép, không tốn tiền API).

```
0:00 ─ WIDE_MODEL      chào, bối cảnh đầy đủ                    (8s)
0:08 ─ MEDIUM_MODEL    giơ sản phẩm lên                          (12s)
0:20 ─ CLOSEUP_PRODUCT cận cảnh nhãn/thành phần (video shop quay) (10s)
0:30 ─ INSET_PRODUCT   vừa nói vừa cho xem chi tiết              (12s)
0:42 ─ SPLIT           bảng giá gốc/giá live                     (10s)
0:52 ─ TRYON_WIDE      mặc/dùng thử (nếu có)                     (15s)
1:07 ─ MEDIUM_MODEL    CTA chốt đơn                              (10s)
```

Quy tắc của planner:
- Đổi shot **mỗi 8-15s**, chỉ cắt **ở ranh giới câu** (dùng thời lượng TTS thật của từng segment).
- Không shot nào quá **20s**. Không 2 shot giống nhau liền nhau.
- `CLOSEUP_PRODUCT` chỉ dùng khi **có** ảnh/video cận của sản phẩm — nếu không có, tự hạ xuống `INSET_PRODUCT`.
- Chuyển cảnh: cắt thẳng (hard cut) hoặc mờ 150ms. **Không** dùng hiệu ứng xoay/lật — trông rẻ tiền.
- Mỗi video **bắt buộc ≥ 3 lần đổi shot** (kiểm ở bước verify, §3.3).

> Thứ làm "giống review thật" nhất, theo thứ tự quan trọng:
> **(1)** có bối cảnh thật · **(2)** có cắt sang cận cảnh sản phẩm · **(3)** có bóng đổ + grade màu · **(4)** lipsync khớp · **(5)** cử chỉ tay.
> Lưu ý: **(1)(2)(3) hoàn toàn không cần AI** — chỉ cần ffmpeg + 1 lần quay bối cảnh + ảnh cận sản phẩm.
> Nên P1 đã có thể đạt mức "nhìn như review", chưa cần chờ P3.

### 4.5 Ba cấp nhà cung cấp (pluggable — code 1 lần, đổi provider bằng cấu hình)

| | **Cấp A — Cloud pre-render** ⭐ | **Cấp B — VTuber realtime** | **Cấp C — Local GPU** |
|---|---|---|---|
| Độ chân thật | Cao (người thật/photoreal) | Thấp (kiểu anime/3D) | Cao |
| Cử chỉ với sản phẩm | ✅ qua i2v + duyệt tay | ⚠️ chỉ animation có sẵn | ✅ |
| Realtime phản hồi bình luận | ❌ (chèn clip) | ✅ | ⚠️ |
| Yêu cầu GPU | **Không** | GPU trung bình (≥6GB) | GPU ≥12GB |
| Chi phí | Theo phút render (trả theo dùng) | Gần 0 sau khi mua model | Chỉ tiền điện + tiền máy |
| Chạy được trên máy hiện tại? | ✅ | ❌ | ❌ |
| Công cụ | HeyGen · D-ID · Sync · Kling/Veo/Hailuo (i2v) | VTube Studio + Live2D (có WebSocket API để bắn hotkey cử chỉ) | Wav2Lip/LatentSync/MuseTalk |

### 4.6 ⚠️ QUYẾT ĐỊNH CẦN BẠN CHỐT

**Khuyến nghị của tôi: Cấp A (cloud pre-render), vì:**
1. Máy hiện tại **không thể** làm local (xem §5) và máy triển khai cũng khó có GPU đủ.
2. Render trước → không có độ trễ, không rủi ro treo giữa phiên LIVE.
3. Chi phí **render 1 lần, phát lại hàng trăm lần** → tính theo sản phẩm, không theo giờ live.
4. Chất lượng đạt mức "người thật", đúng yêu cầu của bạn.

Cần bạn trả lời 4 câu để tôi chốt chi tiết triển khai:
- **(a)** Ngân sách API render/tháng? (ví dụ: dưới 1 triệu / 1-3 triệu / trên 3 triệu VNĐ)
- **(b)** Nhân dạng người mẫu lấy từ đâu? (ảnh của bạn · ảnh mẫu đã ký hợp đồng · avatar có sẵn của nhà cung cấp · ảnh AI sinh)
- **(c)** Ngành hàng chính? (quyết định cue + shot nào cần: `TRY_ON`/`TRYON_WIDE` cho thời trang,
  `SHOW_DETAIL`/`CLOSEUP_PRODUCT` cho mỹ phẩm, `HOLD_PRODUCT` cho gia dụng)
- **(d)** Bối cảnh: bạn **tự quay được** chỗ bán hàng (tốt nhất, 0đ) hay cần tôi dùng **AI sinh bối cảnh**?
  Và chọn 2-3 bối cảnh nào trong danh sách §4.3.1 để làm trước?

> Lưu ý pháp lý/chính sách, cần tuân thủ: **không dùng hình ảnh/giọng người thật mà chưa được phép**;
> nhiều nền tảng yêu cầu **gắn nhãn nội dung do AI tạo** — v2 sẽ có ô bật/tắt chèn nhãn "Nội dung có sử dụng AI" vào banner.

---

## 5. Ràng buộc phần cứng (đã kiểm tra máy hiện tại)

```
CPU   : Intel Xeon E5-2680 v4 @ 2.40GHz (14 core)   → ✅ đủ encode x264 1080×1920@30fps
RAM   : 31.8 GB                                      → ✅ thoải mái
GPU   : NVIDIA GeForce GT 740, 1 GB VRAM (2014)      → ❌ KHÔNG dùng được cho AI
        + Parsec Virtual Display Adapter             → (máy đang điều khiển từ xa)
ffmpeg: C:\Temp\ffmpeg\...\bin\ffmpeg.exe            → ⚠️ nằm trong C:\Temp (dễ bị xoá)
OBS   : chưa cài trên máy này                        → ⚠️
```

**Hệ quả bắt buộc:**
1. **Mọi AI render phải ở cloud.** GT 740 (compute 3.0, 1GB) không chạy được PyTorch hiện đại → gạch bỏ Cấp B và C trên máy này.
2. **Encode stream dùng x264 (CPU)**, không dùng NVENC của GT 740 (chất lượng kém). Preset `veryfast`, bitrate 6000-8000 kbps, keyframe 2s.
3. **ffmpeg phải được đưa vào `tools/ffmpeg/` trong thư mục app** (không để `C:\Temp`) và ghi đường dẫn vào cấu hình.
4. **Cài OBS 30+** trên máy triển khai; preflight phải kiểm tra phiên bản OBS và WebSocket 5.
5. Máy triển khai nên có: ≥4 core, ≥8GB RAM, SSD ≥50GB trống (video render chiếm chỗ), mạng upload ≥10 Mbps ổn định.

---

## 6. Roadmap — 6 giai đoạn

Mỗi giai đoạn có **tiêu chí nghiệm thu đo được**. Không đạt → không sang giai đoạn sau.

### P0 — Cầm máu (1-2 ngày) · sửa trên nền hiện tại, không đổi kiến trúc
- [ ] `strip_stage_directions()` + gọi trước **mọi** lời gọi TTS (lỗi #1)
- [ ] Xoá lựa chọn audio `local` khỏi UI; mặc định = media source trong OBS (lỗi #3)
- [ ] `_play_audio()` trả `False` khi OBS từ chối; `_present()` đánh dấu mục là `failed` (lỗi #5)
- [ ] Sửa `_resume_media()` cho chế độ local, hoặc bỏ hẳn nhánh local (lỗi #4)
- [ ] `pause()` pause luôn video source (lỗi #9)
- [ ] Xoá câu "hãy suy luận thông minh … điền đầy đủ thông số" trong product extractor (lỗi #7)
- [ ] Ẩn 4 "người mẫu" placeholder; thay bằng trạng thái rỗng + hướng dẫn thêm clip thật (lỗi #2)
- [ ] Sửa/vô hiệu hoá D-ID & HeyGen cho tới khi viết lại đúng (lỗi #13, #14)

> **Nghiệm thu P0:** chạy 1 phiên thử 10 phút, 3 sản phẩm → (1) không nghe thấy bất kỳ chữ nào trong ngoặc;
> (2) tiếng phát ra từ OBS (tắt loa máy vẫn thấy tiếng trong bản ghi OBS); (3) đọc 1 câu trả lời bình luận
> xong thì bài giới thiệu **vẫn chạy tiếp**; (4) bấm Tạm dừng → hình **và** tiếng đều đứng.

### P1 — Chuẩn hoá OBS + banner thật (3-5 ngày)
- [ ] `obs_compositor.py`: ép canvas 1080×1920/30fps, kiểm chứng lại
- [ ] Z-order xác định bằng `SetSceneItemIndex`, có test đọc lại chỉ số (lỗi #11)
- [ ] `render/banner.py`: vẽ banner 9:16 bằng Pillow — khung bo góc, nền mờ, giá gốc gạch ngang, giá live cỡ lớn, CTA, phông Việt (Inter/Be Vietnam Pro) (lỗi #12)
- [ ] `preflight.py`: 12 mục chặn cứng (OBS, canvas, nguồn, mute, stream key, dung lượng ổ, ffmpeg, giá verified, mạng)
- [ ] Filler clip + xử lý lỗi không dừng phiên (lỗi #17, #18)
- [ ] *(chuẩn bị vật liệu)* quay/sinh **1 bối cảnh đầu tiên** (video loop 15-20s, 1080×1920) + 3-5 ảnh cận sản phẩm

> **Nghiệm thu P1:** bấm "Bắt đầu LIVE" khi cố ý làm sai 1 trong 12 điều kiện → bị **chặn** kèm thông báo
> nói rõ sai gì, sửa ở đâu. Banner đọc được rõ trên ảnh sáng và ảnh tối. Rút dây mạng giữa phiên →
> hệ thống phát filler, tự kết nối lại, không crash.

### P2 — Kịch bản phân đoạn + BỐI CẢNH + nhịp cắt cảnh (7-9 ngày)
*Giai đoạn này đã cho ra video "nhìn như review thật" — chưa cần người mẫu AI, chưa tốn tiền API render.*
- [ ] LLM trả JSON `segments[]` (text ⊕ cue ⊕ **shot** ⊕ emphasis); có validator + retry khi JSON sai
- [ ] TTS theo từng segment, ffprobe lấy thời lượng **thật**; bắt buộc có ffmpeg (lỗi #6)
- [ ] Xoá hàm ước lượng `filesize/6000` khỏi đường chạy chính (chỉ giữ làm cảnh báo)
- [ ] `sets/scene_set.py` + `library.py`: model dữ liệu bối cảnh (§4.3.1) + 3 bối cảnh đầu tiên
- [ ] Trang **Bối cảnh** mới: nhập video/ảnh nền, kéo đường sàn `floor_y`, đặt điểm đứng, vùng trưng bày, xem thử
- [ ] `render/shot_planner.py`: segments → timeline shot (quy tắc 8-15s, cắt ở ranh giới câu)
- [ ] `render/compositor.py` **v1**: bối cảnh ⊕ ảnh/video sản phẩm ⊕ banner ⊕ audio, cắt theo timeline shot
- [ ] Trang Script Studio: bảng (đoạn | lời nói | cử chỉ | **góc máy** | giây), sửa cue/shot bằng dropdown
- [ ] Ảnh/video cận sản phẩm: UI gắn riêng, dùng cho `CLOSEUP_PRODUCT` (tự hạ cấp nếu thiếu)

> **Nghiệm thu P2:** 1 sản phẩm thật → ra 1 MP4 1080×1920 dài 60-120s, **có bối cảnh thật**,
> **≥ 4 lần đổi góc máy**, có ≥ 1 cảnh cận sản phẩm, banner giá đúng, giọng đọc liền mạch không cắt câu.
> Tổng thời lượng TTS lệch so với ffprobe < 0.5%. Xem trên điện thoại: **cảm giác như một clip review**,
> chỉ thiếu người mẫu trong khung.

### P3 — ★ Người mẫu AI đứng trong bối cảnh + xưởng render (9-14 ngày) — giai đoạn quan trọng nhất
- [ ] `presenter/base.py` + 2 provider đầu tiên (1 lipsync, 1 i2v) theo quyết định ở §4.6
- [ ] `clip_library.py`: quản lý 9 cue/người mẫu; UI nhập clip, xem thử, đánh dấu "đã duyệt"
- [ ] Luồng tạo clip có sản phẩm (§4.2 bước 3) + **bắt buộc duyệt tay** trước khi dùng
- [ ] `sets/matcher.py`: **5 quy tắc chống nhìn-là-biết-ghép** (§4.3.3) + despill/co viền chroma key
- [ ] `compositor.py` **v2**: thêm lớp người mẫu (L2 bóng đổ, L3 người mẫu) vào khung của P2
- [ ] Chế độ 2 "bối cảnh nung" (§4.3.4) cho sản phẩm chủ lực
- [ ] `render/queue.py` + `worker.py` chạy tiến trình riêng (lỗi #19)
- [ ] `verify`: ffprobe kiểm tra kích thước/audio/lệch a-v < 100ms/số lần đổi shot ≥ 3 → fail thì không vào playlist
- [ ] Viết lại D-ID/HeyGen provider cho đúng API (có poll + tải file về + xử lý lỗi)

> **Nghiệm thu P3:** chọn 1 sản phẩm thật của shop → bấm "Tạo video người mẫu giới thiệu" →
> nhận 1 MP4 1080×1920 trong đó: người mẫu **đứng trong bối cảnh** (chân đúng mặt sàn, **có bóng đổ**,
> màu da khớp ánh sáng của set), **giơ đúng sản phẩm đó**, miệng khớp lời, ≥3 cử chỉ, ≥4 lần đổi góc máy,
> banner giá đúng, tổng 60-120s.
> **Phép thử gắt:** cho 5 người lạ xem trên điện thoại → không quá 1 người nhận ra là ảnh ghép.
> UI **không treo** trong suốt quá trình render.

### P4 — Playout v2 (4-6 ngày)
- [ ] Viết lại `scheduler_service.py` → `playout/engine.py` (chỉ phát MP4, theo trạng thái thật từ OBS)
- [ ] Hàng đợi chèn: clip trả lời bình luận, clip flash sale, filler
- [ ] Giám sát: dropped frames, bitrate, mục đang phát, còn bao lâu, nhật ký
- [ ] Báo cáo sau phiên

> **Nghiệm thu P4:** chạy **90 phút liên tục**, 8 sản phẩm × 2 vòng → 0 lần màn hình trống,
> 0 lần lệch tiếng/hình, khoảng nghỉ giữa 2 mục < 1s, bộ nhớ app không tăng quá 150MB,
> cố ý xoá 1 file MP4 giữa phiên → chuyển mục kế tiếp êm, có log, phiên **không dừng**.

### P5 — Vòng bình luận thật (5-7 ngày)
- [ ] `screen_ocr.py`: chọn vùng, chụp định kỳ, OCR tiếng Việt, chống trùng
- [ ] Phân loại + trả lời có căn cứ (chỉ từ dữ liệu đã verified)
- [ ] Danh sách trắng tự động trả lời (giá, size, ship, bảo hành) + ngưỡng tin cậy
- [ ] Ngân hàng clip trả lời (`NOD_LISTEN` + TTS) chèn vào playout

> **Nghiệm thu P5:** phiên 30 phút với 20 bình luận thật → OCR bắt được ≥ 17/20,
> 0 bình luận bị xử lý trùng, thời gian từ lúc khách gõ đến lúc nghe câu trả lời trên sóng < 25s,
> 0 câu trả lời chứa thông tin không có trong dữ liệu sản phẩm (kiểm tra tay toàn bộ).

### P6 — Đóng gói & triển khai máy khác (3-5 ngày)
- [ ] `tools/ffmpeg/` đi kèm app; `doctor.bat` kiểm tra môi trường (OBS, phông Việt, ổ đĩa, mạng, GPU)
- [ ] Installer (Inno Setup) + tự tạo scene OBS lần đầu + wizard onboarding 5 bước
- [ ] Hướng dẫn vận hành 1 trang + checklist trước mỗi phiên LIVE
- [ ] Nhật ký có xoay vòng file, nút "xuất gói log" để gửi khi cần hỗ trợ

> **Nghiệm thu P6:** máy Windows sạch (chưa có Python) → cài trong < 15 phút → lên sóng thật thành công, không cần can thiệp dòng lệnh.

**Tổng: ~32-48 ngày làm việc.** Mốc có thể dùng thật:
**sau P1** = lên sóng cơ bản ổn định · **sau P2** = video có bối cảnh + nhịp cắt kiểu review (chưa có người mẫu)
· **sau P3** = đầy đủ người mẫu AI trong bối cảnh · **sau P5** = auto-live thật sự (tự trả lời bình luận).

---

## 7. Danh sách phải XOÁ hoặc VIẾT LẠI

| Tệp / hàm | Xử lý | Lý do |
|---|---|---|
| `avatar_service._generate_avatar_art` + 4 preset | **XOÁ** | Placeholder hình tròn, không thể lên sóng (lỗi #2) |
| `avatar_service._generate_heygen_video` | **VIẾT LẠI** | Không poll, không tải về, `avatar_id` sai (lỗi #14) |
| `avatar_service._generate_did_video` | **VIẾT LẠI** | Thiếu `source_url`, không nhận path local (lỗi #13) |
| `scheduler_service.py` (toàn bộ) | **VIẾT LẠI** → `playout/engine.py` | Kiến trúc đồng bộ realtime là nguồn gốc lỗi #4,5,6,8,9 |
| `LocalAudioPlayer` + chế độ audio `local` | **XOÁ** | Nguồn gốc lỗi #3, #4 |
| `voice_service.estimate_mp3_duration` (đường chạy chính) | **HẠ CẤP** còn cảnh báo | Ước lượng `filesize/6000` gây lệch (lỗi #6) |
| `obs/client._place_text_bottom` | **THAY** bằng `render/banner.py` | Text trần không đọc được (lỗi #12) |
| `product_extractor_service` — câu "hãy suy luận thông minh…" | **XOÁ** | Khiến AI bịa giá/thông số (lỗi #7) |
| `product_showcase_dialog._on_send_to_obs` (asyncio trong UI thread) | **VIẾT LẠI** dùng worker | Treo app giữa phiên LIVE (lỗi #19) |
| `app/workers/` (đang rỗng) | **DÙNG THẬT** | Chỗ đặt render worker / OCR worker |

**Giữ nguyên (phần làm tốt):** `database/` (models, repositories, migrations có version), `core/` (logging, secrets qua
Credential Manager, exceptions), `integrations/llm/`, `integrations/tts/`, `integrations/ffmpeg/`, `tiktok/capabilities.py`
(ma trận capability trung thực — rất đáng giữ), `ui/styles/`, và bộ 53 test hiện có.

---

## 8. Chi phí & rủi ro

### 8.1 Chi phí (ước lượng — **phải kiểm chứng giá tại thời điểm mua**, giá API thay đổi liên tục)

| Khoản | Mức | Ghi chú |
|---|---|---|
| TTS (Edge TTS) | 0đ | Miễn phí, cần internet. Nếu muốn giọng cao cấp hơn → trả phí theo ký tự |
| LLM viết kịch bản | Rất thấp | ~2-5k token/sản phẩm |
| **Bối cảnh (set)** | **0đ nếu tự quay** | Quay 15-20s/bối cảnh bằng điện thoại, dùng vĩnh viễn. AI sinh ảnh nền thì rất rẻ |
| Ảnh/video cận sản phẩm | 0đ | Shop tự chụp/quay — và đây là thứ làm "giống review thật" nhiều nhất |
| Render clip cử chỉ người mẫu | **Lớn nhất** | Tính theo giây video. Làm **1 lần/người mẫu** (9-15 clip) rồi dùng mãi |
| Clip có sản phẩm (i2v) | Trung bình | Mỗi sản phẩm 1-3 lần render + duyệt tay |
| Lipsync | Thấp-trung bình | Mỗi lần đổi kịch bản/giá phải lipsync lại |
| Máy chạy LIVE | Tiền máy + mạng | Không cần GPU theo kiến trúc này |

**Cách giảm chi phí (phải code vào hệ thống):** cache theo hash (text+voice+cue+avatar) — giống cache TTS đang có;
đổi giá mà không đổi lời → **chỉ render lại banner**, không render lại người mẫu; dùng `IDLE_TALK` cho các đoạn
không cần cử chỉ đặc biệt.

### 8.2 Rủi ro

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Clip i2v ra tay/ngón dị dạng, sản phẩm bị méo nhãn | **Cao** | **Duyệt tay bắt buộc** + render 3 biến thể chọn 1 + không cho clip chưa duyệt vào playlist |
| Người mẫu nhìn như **dán hình** vào nền | **Cao** | 5 quy tắc §4.3.3 là bắt buộc, có kiểm tra tự động (chân đúng `floor_y`, có lớp bóng đổ, đã grade màu); sản phẩm chủ lực dùng Chế độ 2 "bối cảnh nung" |
| Nhà cung cấp API đổi giá/điều khoản/đóng cửa | Trung bình | Giao diện `PresenterProvider` → đổi provider bằng cấu hình, không sửa code lõi |
| Nền tảng siết nội dung AI / yêu cầu gắn nhãn | Trung bình | Có sẵn tuỳ chọn chèn nhãn "có sử dụng AI"; theo dõi chính sách |
| OCR đọc sai bình luận → trả lời sai | Trung bình | Ngưỡng tin cậy + duyệt tay cho câu có số (giá/size) + danh sách trắng hẹp |
| Dùng hình ảnh/giọng người thật chưa được phép | **Cao (pháp lý)** | Bắt buộc tích xác nhận quyền sử dụng khi tạo người mẫu; lưu nguồn ảnh |
| Điều kiện LIVE qua OBS của TikTok/Shopee thay đổi | Trung bình | Giữ ma trận capability trung thực; không hứa điều nền tảng không cho |
| Ổ cứng đầy vì video render | Trung bình | Dọn tự động bản render cũ, giới hạn dung lượng, cảnh báo ở preflight |

---

## 9. Bài test nghiệm thu cuối: LIVE THẬT 60 PHÚT

Hệ thống chỉ được coi là "chạy đúng thực tế" khi **vượt toàn bộ** bài test này trên máy triển khai:

**Chuẩn bị:** 6 sản phẩm thật (ảnh + giá đã xác minh + ảnh cận), **2 bối cảnh đã cấu hình**,
1 người mẫu AI với 9 clip cue đã duyệt, 6 video sản phẩm đã render & duyệt, OBS 1080×1920,
stream key thật, playlist lặp vô hạn.

| # | Kiểm tra | Đạt khi |
|---|---|---|
| 1 | Preflight | Chặn được cả 12 lỗi khi cố ý tạo lỗi; báo rõ nguyên nhân |
| 2 | Lên sóng | Phòng LIVE thật nhận hình **dọc, không viền đen**, trong 30s |
| 3 | Âm thanh | Nghe rõ giọng người mẫu; **tắt loa máy** vẫn có tiếng trên sóng; **không** lẫn tiếng Windows/Zalo |
| 4 | Lời nói | 60 phút **không** nghe bất kỳ chữ nào trong ngoặc; không câu nào bị cắt giữa |
| 5 | Người mẫu | Miệng khớp lời; mỗi sản phẩm có ≥3 cử chỉ; có ≥1 cảnh **cầm/chỉ đúng sản phẩm đó** |
| 5b | **Bối cảnh** | Người mẫu đứng đúng mặt sàn, **có bóng đổ**, màu khớp ánh sáng set; không thấy viền xanh quanh tóc |
| 5c | **Nhịp cắt cảnh** | Mỗi sản phẩm đổi góc máy ≥3 lần; không shot nào quá 20s; không cắt giữa câu |
| 6 | Banner | Tên + giá đúng 100% so với dữ liệu; đọc được rõ trên điện thoại |
| 7 | Chuyển mục | 12 lần chuyển (6 sp × 2 vòng), mỗi lần nghỉ < 1s, không màn hình trống |
| 8 | Bình luận | 15 bình luận thật → ≥12 được bắt; trả lời lên sóng < 25s; **0 câu bịa thông tin** |
| 9 | Điều khiển | Tạm dừng → đứng cả hình+tiếng; Tiếp tục → đúng chỗ cũ; Bỏ qua → sang mục kế |
| 10 | Chịu lỗi | Xoá 1 MP4 + tắt OBS 20s giữa phiên → phát filler, tự kết nối lại, phiên **không dừng** |
| 11 | Ổn định | 60 phút: dropped frames < 1%, RAM app tăng < 150MB, 0 crash, 0 "Not Responding" |
| 12 | Báo cáo | Sau phiên có báo cáo đúng: số sản phẩm, số vòng, lỗi, bình luận chưa trả lời |

---

## 10. Việc tiếp theo

1. Bạn trả lời **4 câu ở §4.6** (ngân sách · nguồn nhân dạng người mẫu · ngành hàng · bối cảnh).
2. Tôi làm **P0 ngay** (1-2 ngày) — P0 không phụ thuộc câu trả lời nào và sửa đúng những lỗi đang làm bạn không dùng được.
3. Song song: tôi dựng `sets/` + `render/shot_planner.py` + `compositor` v1 (phần **không phụ thuộc nhà cung cấp nào**,
   không tốn tiền API) — đây là phần tạo ra "cảm giác review thật" và sẽ chạy được ngay cả khi bạn chưa chốt provider.

> **Vật liệu cần bạn gửi để bắt đầu:**
> - **1 sản phẩm thật** — ảnh toàn cảnh + **2-3 ảnh/clip cận** (nhãn, chất liệu, chi tiết) + giá gốc & giá live + mô tả.
>   Đây sẽ là "sản phẩm mẫu vàng", mọi giai đoạn đều nghiệm thu trên chính nó nên bạn thấy rõ tiến bộ từng bước.
> - **1 video bối cảnh 15-20s** quay bằng điện thoại **theo chiều dọc**, camera **đặt cố định** (kê lên đâu đó, không cầm tay),
>   quay chỗ bạn bán hàng / góc phòng / bàn trưng bày. Chừa khoảng trống bên phải hoặc giữa khung để đặt người mẫu.
>   Nếu chưa quay được, nói tôi biết để dùng bối cảnh AI sinh.
