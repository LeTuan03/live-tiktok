# MASTER PROMPT — BUILD AI TIKTOK LIVE COMMERCE STUDIO WITH PYTHON

## 1. Vai trò

Bạn là một Senior Python Desktop Engineer, AI Engineer và Software Architect có kinh nghiệm xây dựng ứng dụng Windows, xử lý media, tích hợp API, AI Voice, database và hệ thống chạy nền.

Nhiệm vụ của bạn là **xây dựng một ứng dụng desktop Windows hoàn chỉnh từ đầu đến cuối**, không chỉ viết tài liệu hoặc tạo prototype giao diện.

Ứng dụng dùng AI để hỗ trợ livestream bán hàng trên TikTok theo danh sách sản phẩm do người dùng quản lý.

Tên dự án: **LiveFlow AI Studio**

Ngôn ngữ giao diện: Tiếng Việt.

Ngôn ngữ lập trình chính: Python.

Hệ điều hành mục tiêu: Windows 10/11 64-bit.

Ưu tiên: ổn định, dễ sử dụng, tiết kiệm tài nguyên, có khả năng mở rộng và đóng gói thành bộ cài Windows.

---

## 2. Mục tiêu sản phẩm

Xây dựng công cụ hỗ trợ người bán vận hành livestream theo quy trình:

1. Người dùng nhập danh sách sản phẩm.
2. Người dùng thêm video giới thiệu hoặc video trình chiếu sản phẩm.
3. AI đọc thông tin sản phẩm và tạo kịch bản giới thiệu.
4. TTS chuyển kịch bản thành giọng nói tự nhiên.
5. Ứng dụng điều phối video, âm thanh và thứ tự giới thiệu sản phẩm.
6. OBS Studio đảm nhiệm việc phát video và livestream.
7. Ứng dụng hỗ trợ theo dõi bình luận và đề xuất câu trả lời bằng AI.
8. Người dùng có thể duyệt câu trả lời trước khi gửi.
9. Ứng dụng quản lý trạng thái phiên LIVE, lịch phát, nhật ký và lỗi.

**Không được giả định rằng TikTok cung cấp API công khai cho mọi chức năng livestream.** Mọi chức năng đọc/gửi bình luận, ghim sản phẩm hoặc điều khiển LIVE phải được kiểm tra khả năng tích hợp thực tế trước khi triển khai.

---

## 3. Công nghệ bắt buộc

### Desktop UI

* Python 3.12+
* PySide6 / Qt
* Qt Designer nếu thực sự hữu ích; không bắt buộc.
* Giao diện responsive theo kích thước cửa sổ.
* Hỗ trợ Windows Dark Mode hoặc giao diện tối hiện đại.

### Backend

* Python
* asyncio cho tác vụ bất đồng bộ.
* QThread hoặc cơ chế worker phù hợp để không block UI.
* Pydantic cho validation dữ liệu.
* SQLAlchemy 2.x.
* SQLite làm database mặc định.

### Media

* OBS Studio.
* OBS WebSocket 5.x.
* FFmpeg và ffprobe.
* Không tự viết video encoder nếu không cần thiết.
* Không decode hoặc xử lý video nặng trên UI thread.

### AI

Thiết kế provider abstraction để có thể thay đổi nhà cung cấp:

* LLM provider cho tạo kịch bản và xử lý bình luận.
* TTS provider cho tạo giọng nói.
* Có thể bổ sung provider local trong tương lai.

Không hardcode API key trong source code.

### Packaging

* PyInstaller hoặc Nuitka.
* Tạo bộ cài Windows.
* Có hướng dẫn cài đặt OBS và FFmpeg.
* Có kiểm tra dependency khi khởi động.

---

## 4. Kiến trúc hệ thống

Thiết kế theo module, tách biệt UI, business logic, database và integration.

Cấu trúc tham khảo:

```text
liveflow-ai-studio/
├── app/
│   ├── main.py
│   ├── bootstrap.py
│   ├── config/
│   ├── core/
│   │   ├── events.py
│   │   ├── task_manager.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   ├── database/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── migrations/
│   │   └── session.py
│   ├── services/
│   │   ├── product_service.py
│   │   ├── script_service.py
│   │   ├── playlist_service.py
│   │   ├── tts_service.py
│   │   ├── live_session_service.py
│   │   └── comment_service.py
│   ├── integrations/
│   │   ├── obs/
│   │   ├── ffmpeg/
│   │   ├── llm/
│   │   ├── tts/
│   │   └── tiktok/
│   ├── workers/
│   ├── ui/
│   │   ├── windows/
│   │   ├── pages/
│   │   ├── dialogs/
│   │   ├── components/
│   │   └── styles/
│   └── utils/
├── assets/
├── tests/
├── scripts/
├── docs/
├── requirements.txt
├── pyproject.toml
└── README.md
```

Có thể điều chỉnh cấu trúc nếu có lý do kỹ thuật rõ ràng. Không tạo quá nhiều abstraction không cần thiết.

---

## 5. Các màn hình bắt buộc

### 5.1 Dashboard

Hiển thị:

* Trạng thái ứng dụng.
* Trạng thái OBS.
* Trạng thái kết nối TikTok nếu có tích hợp hợp lệ.
* Số lượng sản phẩm.
* Số lượng video.
* Phiên LIVE gần nhất.
* Tình trạng hàng đợi TTS.
* Nhật ký cảnh báo và lỗi gần đây.

### 5.2 Quản lý sản phẩm

Cho phép:

* Thêm, sửa, xóa sản phẩm.
* Tìm kiếm và lọc sản phẩm.
* Import/export CSV.
* Bật/tắt sản phẩm.
* Sắp xếp thứ tự giới thiệu.
* Gắn video vào sản phẩm.
* Gắn kịch bản riêng vào sản phẩm.

Thông tin sản phẩm gồm:

* Tên sản phẩm.
* Mã sản phẩm/SKU.
* Danh mục.
* Giá bán.
* Giá khuyến mãi nếu có.
* Mô tả.
* Tính năng đã được xác minh.
* Thành phần/chất liệu nếu có.
* Kích thước hoặc biến thể.
* Đối tượng sử dụng.
* Hướng dẫn sử dụng.
* Lưu ý/cảnh báo.
* URL sản phẩm.
* Đường dẫn ảnh.
* Đường dẫn video.
* Trạng thái hoạt động.

AI chỉ được sử dụng thông tin có trong dữ liệu sản phẩm hoặc nguồn người dùng đã cung cấp. Không tự bịa công dụng, chứng nhận, giá, ưu đãi, tồn kho hoặc cam kết hiệu quả.

### 5.3 Media Library

Cho phép:

* Import video và audio.
* Xem metadata bằng ffprobe.
* Hiển thị thời lượng, độ phân giải, codec, dung lượng.
* Preview video.
* Gắn video với một hoặc nhiều sản phẩm.
* Kiểm tra file bị thiếu hoặc không đọc được.
* Tạo playlist.
* Phát tuần tự hoặc lặp lại playlist.

Không tự ý chỉnh sửa hoặc xóa file gốc của người dùng.

### 5.4 AI Script Studio

Cho phép tạo kịch bản giới thiệu sản phẩm theo cấu hình:

* Thời lượng: 15, 30, 60, 90 giây hoặc tùy chỉnh.
* Phong cách: thân thiện, tư vấn, ngắn gọn, năng động.
* Đối tượng người xem.
* Tốc độ nói.
* Ngôn ngữ.
* Cách xưng hô.
* CTA phù hợp.
* Mức độ lặp lại nội dung.

Kịch bản phải có cấu trúc:

1. Câu mở đầu.
2. Nêu tên sản phẩm.
3. Giới thiệu các đặc điểm đã xác minh.
4. Lợi ích diễn đạt thận trọng, không phóng đại.
5. Hướng dẫn hoặc lưu ý cần thiết.
6. CTA.

Cho phép người dùng chỉnh sửa, lưu phiên bản và tạo lại kịch bản.

### 5.5 AI Voice Studio

Cho phép:

* Chọn TTS provider.
* Chọn giọng đọc có sẵn từ provider.
* Điều chỉnh tốc độ và âm lượng nếu provider hỗ trợ.
* Preview audio.
* Tạo audio từ kịch bản.
* Lưu audio vào thư viện.
* Cache theo nội dung và cấu hình giọng đọc.
* Không tạo lại audio nếu nội dung không thay đổi và cache còn hợp lệ.

Nếu TTS provider lỗi, hiển thị lỗi rõ ràng và cho phép thử lại.

### 5.6 Live Studio

Hiển thị:

* Preview từ OBS nếu khả thi.
* Trạng thái OBS.
* Scene đang hoạt động.
* Nguồn media.
* Sản phẩm hiện tại.
* Video hiện tại.
* Kịch bản hiện tại.
* Tình trạng TTS.
* Hàng đợi sản phẩm tiếp theo.
* Nút bắt đầu/tạm dừng/tiếp tục/dừng điều phối nội dung.

Ứng dụng không được tự ý kết thúc OBS hoặc kill tiến trình OBS khi người dùng đóng ứng dụng.

### 5.7 Product Presentation Scheduler

Cho phép cấu hình:

* Danh sách sản phẩm.
* Thứ tự giới thiệu.
* Thời lượng mỗi sản phẩm.
* Video tương ứng.
* Audio TTS tương ứng.
* Khoảng nghỉ giữa các lần giới thiệu.
* Số lần lặp.
* Lịch bắt đầu và kết thúc.
* Chế độ phát tuần tự hoặc lặp playlist.

Scheduler phải có:

* Pause/resume.
* Stop an toàn.
* Trạng thái hiện tại.
* Xử lý khi một video/audio bị lỗi.
* Không phát đồng thời nhiều TTS nếu chưa được cấu hình.
* Không tạo vòng lặp task vô hạn khi có lỗi.

### 5.8 AI Comment Assistant

Nếu có nguồn bình luận được tích hợp hợp lệ:

* Nhận bình luận.
* Phân loại: hỏi giá, hỏi tính năng, hỏi vận chuyển, hỏi biến thể, spam, nội dung không phù hợp, câu hỏi khác.
* Tìm câu trả lời dựa trên dữ liệu sản phẩm.
* Đề xuất câu trả lời ngắn, tự nhiên, đúng ngữ cảnh.
* Cho phép người vận hành duyệt trước khi gửi.
* Có thể bật auto-reply riêng cho nhóm câu hỏi rủi ro thấp nếu nền tảng và quyền tích hợp cho phép.

Mặc định:

* Auto-reply tắt.
* Người dùng phải bật rõ ràng.
* Có giới hạn tốc độ gửi.
* Có log nội dung và trạng thái gửi.
* Không trả lời khi thiếu dữ liệu xác minh.
* Không đưa ra cam kết về sức khỏe, tài chính, hiệu quả sản phẩm hoặc chính sách không có nguồn.
* Không gửi câu trả lời nếu chưa xác nhận được quyền gửi qua integration.

Nếu không có API hợp lệ, triển khai chế độ **Comment Assistant thủ công**: người dùng nhập/dán bình luận, AI đề xuất câu trả lời để người dùng tự gửi trên giao diện TikTok.

---

## 6. Tích hợp TikTok — yêu cầu bắt buộc

Thiết kế TikTok integration theo capability-based architecture.

Mỗi tính năng phải có trạng thái:

* Supported: đã xác minh và triển khai bằng phương thức được phép.
* Unsupported: không có phương thức tích hợp khả dụng.
* Requires Authorization: cần quyền hoặc thông tin xác thực.
* Manual Mode: người dùng thao tác thủ công.
* Demo: dữ liệu giả lập, chỉ dùng kiểm thử.

Không được:

* Bịa API endpoint.
* Giả lập thành công khi API chưa thực hiện.
* Thu thập hoặc lưu mật khẩu TikTok.
* Scrape private API hoặc reverse-engineer giao thức riêng tư.
* Vượt CAPTCHA, OTP hoặc challenge bảo mật.
* Tự động né giới hạn hoặc cơ chế chống lạm dụng.
* Dùng browser automation để giả mạo thao tác người dùng nhằm vượt hạn chế nền tảng.

Nếu cần đăng nhập hoặc xác minh bảo mật, người dùng tự thao tác trên giao diện chính thức.

### Product Pinning

Chỉ triển khai tự động ghim sản phẩm khi:

1. Có API hoặc phương thức tích hợp chính thức/được cho phép.
2. Có quyền truy cập phù hợp.
3. Có xác nhận thành công từ nền tảng.

Nếu không đủ điều kiện:

* Hiển thị hướng dẫn ghim sản phẩm thủ công.
* Cho phép người dùng đánh dấu sản phẩm đã ghim.
* Không hiển thị trạng thái “đã ghim” nếu chưa được xác nhận.

---

## 7. OBS Integration

Dùng OBS WebSocket để:

* Kiểm tra OBS đang chạy.
* Kết nối/ngắt kết nối.
* Đọc danh sách scene.
* Đọc trạng thái streaming.
* Chọn scene nếu quyền và API hỗ trợ.
* Điều khiển media source nếu cấu hình phù hợp.
* Hiển thị lỗi kết nối.
* Tự reconnect có giới hạn và backoff.

Không lưu OBS password dạng plaintext.

Không tự tạo cấu hình OBS phá hủy cấu hình hiện có. Trước khi thay đổi scene/source, cần xác nhận hoặc tạo cấu hình riêng.

---

## 8. Bảo mật và quyền riêng tư

* Không hardcode secret trong source code.
* Không ghi API key, token, password vào log.
* Dùng Windows Credential Manager hoặc cơ chế lưu trữ bí mật phù hợp.
* Validate dữ liệu đầu vào.
* Giới hạn kích thước file import.
* Kiểm tra đường dẫn file.
* Xử lý lỗi không làm crash toàn ứng dụng.
* Có chức năng xóa dữ liệu người dùng.
* Không gửi dữ liệu sản phẩm hoặc bình luận tới AI provider nếu chưa cấu hình provider và có thông báo rõ ràng.

---

## 9. Tối ưu hiệu năng

Mục tiêu thiết kế:

* UI không bị treo khi phát video, gọi AI hoặc tạo TTS.
* Video playback do OBS/FFmpeg xử lý.
* Không load toàn bộ video vào RAM.
* Dùng streaming/chunking khi phù hợp.
* Cache metadata và audio.
* Giới hạn số tác vụ AI đồng thời.
* Có hàng đợi tác vụ.
* Có timeout và retry có giới hạn.
* Có log mức độ INFO/WARNING/ERROR.
* Có chế độ chạy nền System Tray.
* Có thể thu nhỏ xuống tray.
* Khi thoát, dừng worker và đóng database an toàn.

Không tuyên bố mức sử dụng RAM/CPU cụ thể nếu chưa đo kiểm trên máy thực tế. Hãy tạo benchmark và ghi kết quả.

---

## 10. Database

Thiết kế schema và migration cho tối thiểu:

* products
* product_variants
* media_files
* product_media
* scripts
* script_versions
* voice_profiles
* generated_audio
* playlists
* playlist_items
* live_sessions
* scheduler_jobs
* comment_events
* ai_suggestions
* integration_settings
* app_settings
* activity_logs

Dùng foreign key, index phù hợp và migration có version.

Không dùng mock data trong production. Demo data phải nằm trong chế độ demo riêng.

---

## 11. Testing

Viết test cho:

* CRUD sản phẩm.
* Import/export CSV.
* Validation dữ liệu sản phẩm.
* Tạo kịch bản từ dữ liệu sản phẩm.
* Chặn AI tạo claim không có nguồn.
* TTS cache.
* Playlist scheduler.
* Pause/resume/stop.
* OBS connection failure.
* TikTok capability gating.
* Không báo gửi bình luận thành công khi chưa có xác nhận.
* Không báo ghim sản phẩm thành công khi chưa có xác nhận.
* Database migration.
* Xử lý file media không hợp lệ.

Dùng pytest.

Có lint/type checking phù hợp, ví dụ Ruff và mypy/pyright nếu cấu hình khả thi.

---

## 12. Quy trình triển khai

Không dừng lại ở việc đưa ra kế hoạch. Hãy kiểm tra repository hiện tại và bắt đầu triển khai.

### Phase 0 — Repository Inspection

* Kiểm tra thư mục dự án.
* Kiểm tra Python và dependency.
* Kiểm tra Git status.
* Không ghi đè hoặc xóa file người dùng nếu chưa hiểu mục đích.
* Tạo implementation plan ngắn.

### Phase 1 — Foundation

* Khởi tạo project.
* Cấu hình dependency.
* Tạo cấu trúc module.
* Tạo logging, settings, database và migration.
* Tạo cửa sổ PySide6 chính.

### Phase 2 — Product & Media

* CRUD sản phẩm.
* Import/export CSV.
* Media library.
* ffprobe metadata.
* Gắn video với sản phẩm.

### Phase 3 — AI Script & TTS

* Provider abstraction.
* Tạo kịch bản theo dữ liệu sản phẩm.
* Chỉnh sửa/lưu kịch bản.
* Tạo audio.
* Cache audio.

### Phase 4 — OBS & Scheduler

* OBS WebSocket integration.
* Playlist.
* Scheduler.
* Trạng thái phiên.
* Pause/resume/stop.
* Xử lý lỗi.

### Phase 5 — Comment Assistant

* Xây dựng comment workflow.
* AI đề xuất câu trả lời có căn cứ.
* Manual approval.
* Capability gating cho gửi bình luận.

### Phase 6 — TikTok Integration

* Chỉ triển khai các capability đã xác minh.
* Nếu thiếu API/quyền, hoàn thiện manual mode.
* Viết tài liệu nêu rõ tính năng nào hoạt động, tính năng nào cần quyền hoặc chưa khả dụng.

### Phase 7 — Quality & Release

* Chạy tests.
* Chạy lint/type checks.
* Build ứng dụng.
* Kiểm tra startup/shutdown.
* Kiểm tra tray.
* Đóng gói Windows.
* Viết README tiếng Việt.
* Viết hướng dẫn cài OBS, cấu hình TTS/AI và sử dụng ứng dụng.
* Ghi rõ các giới hạn tích hợp TikTok.

Sau mỗi phase:

1. Báo cáo file đã tạo/sửa.
2. Chạy kiểm tra phù hợp.
3. Sửa lỗi phát sinh.
4. Không đánh dấu hoàn thành nếu chưa kiểm chứng.

---

## 13. Tiêu chí nghiệm thu

Ứng dụng được xem là hoàn thành khi:

* Chạy được trên Windows 10/11.
* Có giao diện desktop tiếng Việt.
* Quản lý được sản phẩm.
* Import được video.
* Tạo được kịch bản dựa trên dữ liệu sản phẩm.
* Tạo và phát được audio TTS.
* Quản lý được playlist sản phẩm.
* Kết nối OBS khi OBS WebSocket được cấu hình.
* Có scheduler với pause/resume/stop.
* Có comment assistant và chế độ duyệt thủ công.
* Có capability status rõ ràng cho từng tính năng TikTok.
* Không báo thành công giả cho thao tác nền tảng.
* Có logging, xử lý lỗi và lưu cấu hình.
* Có test cơ bản.
* Có hướng dẫn chạy và đóng gói.

**Ưu tiên hoàn thiện luồng cốt lõi: Product → Script → TTS → Video/Playlist → OBS.** Không để việc thiếu quyền API TikTok làm đình trệ các chức năng độc lập này.

Bắt đầu bằng việc kiểm tra repository và triển khai Phase 0, sau đó tiếp tục xây dựng các phase theo thứ tự. Nếu gặp giới hạn API hoặc quyền truy cập bên ngoài, hãy ghi rõ giới hạn và tiếp tục hoàn thiện phần có thể triển khai hợp lệ.