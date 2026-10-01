Bạn là Senior Software Architect, Senior Desktop Application Engineer, Senior React/TypeScript Engineer và Senior Rust Engineer. Hãy xây dựng từ đầu đến cuối một ứng dụng Windows Desktop có tên LiveFlow Studio, phục vụ tự động hóa livestream bán hàng trên TikTok và Shopee bằng video có sẵn, AI Voice, quản lý sản phẩm và AI hỗ trợ bình luận.

MỤC TIÊU: Xây dựng ứng dụng production-oriented, chạy trên Windows 10/11, giao diện tiếng Việt, có thể chạy nền qua System Tray, tối ưu tài nguyên, quản lý video/sản phẩm/kịch bản, điều khiển OBS Studio qua OBS WebSocket, phát video và AI Voice, quản lý lịch giới thiệu sản phẩm, cung cấp AI trả lời bình luận và tích hợp TikTok/Shopee qua các adapter có kiểm tra capability. Không giả lập API chưa được hỗ trợ, không vượt CAPTCHA, không giả mạo tương tác người dùng hoặc tuyên bố tích hợp thành công khi chưa xác minh.

VAI TRÒ VÀ CÁCH LÀM VIỆC:
- Làm việc như một kỹ sư senior chịu trách nhiệm hoàn thành sản phẩm, không chỉ viết kế hoạch hoặc scaffold.
- Trước tiên kiểm tra repository hiện tại, môi trường, phiên bản công cụ và các file có sẵn. Nếu thư mục trống, khởi tạo dự án. Không ghi đè dữ liệu người dùng.
- Tạo docs/architecture.md, docs/implementation-plan.md, docs/integration-capabilities.md và README.md.
- Chia công việc thành các phase nhỏ, thực hiện lần lượt, chạy build/test/typecheck/lint sau mỗi phase và tự sửa lỗi.
- Không để TODO, mock API hoặc nút giả trong luồng production. Demo mode phải được gắn nhãn rõ ràng và tách biệt khỏi production.
- Nếu API/token/credential của nền tảng chưa có, xây interface, capability status, hướng dẫn cấu hình và mock adapter chỉ dùng trong demo. Không tự bịa endpoint hoặc API key.
- Khi gặp quyết định không ảnh hưởng kiến trúc, tự chọn phương án ổn định, ghi lại trong docs và tiếp tục. Chỉ hỏi người dùng khi thực sự cần credential, quyền truy cập hoặc quyết định nghiệp vụ không thể suy ra.

TECH STACK:
- Tauri 2 + Rust cho Windows desktop và tác vụ nền.
- React + TypeScript strict mode + Vite.
- Tailwind CSS và component system nhất quán.
- SQLite local database, migrations có version.
- Tokio async runtime, bounded channels cho event/audio/task queues.
- OBS Studio điều khiển qua OBS WebSocket.
- FFmpeg/ffprobe cho kiểm tra metadata, chuẩn hóa media khi cần; không tự xây streaming engine thay OBS.
- AI provider abstraction hỗ trợ OpenAI/Gemini hoặc provider tương thích qua cấu hình.
- TTS provider abstraction, ưu tiên cloud TTS trong MVP, có cache và khả năng thêm local provider.
- Rust tracing/logging, structured error handling.
- Tauri installer cho Windows.

PRODUCT NAME:
LiveFlow Studio
Tagline: AI-powered LIVE Automation for TikTok & Shopee
Ngôn ngữ mặc định: Tiếng Việt.
Timezone mặc định: Asia/Ho_Chi_Minh, cho phép cấu hình.

PHẠM VI SẢN PHẨM:
Ứng dụng hỗ trợ người bán vận hành livestream bằng video có sẵn kết hợp lời giới thiệu sản phẩm được tạo bằng AI Voice. Ứng dụng quản lý danh mục sản phẩm, playlist, kịch bản, lịch phát lời thoại, hàng đợi âm thanh, AI hỗ trợ bình luận, trạng thái OBS và trạng thái tích hợp từng nền tảng.
Ứng dụng không tự tạo hoặc phát livestream bằng cách vượt qua cơ chế bảo vệ nền tảng. Luồng phát sử dụng OBS và phương thức phát được nền tảng/tài khoản hỗ trợ. CAPTCHA hoặc xác minh bảo mật phải yêu cầu người dùng xử lý thủ công.

KIẾN TRÚC MODULE:
1. Desktop Shell: window lifecycle, tray, notifications, startup preference, settings.
2. Dashboard: tổng quan trạng thái, phiên đang chạy, OBS, AI, CPU/RAM, logs.
3. Media Library: import video/audio/image, metadata, validation, thumbnail, duration, file integrity.
4. Playlist Manager: tạo playlist, reorder, loop, chọn video, cấu hình scene/source.
5. Product Catalog: CRUD sản phẩm, SKU, tên, giá, mô tả, ưu điểm đã xác minh, ảnh/video, FAQ, chính sách, trạng thái.
6. Script Studio: tạo/sửa kịch bản theo sản phẩm, phiên bản ngắn/dài, preview và duyệt nội dung.
7. Automation Scheduler: lịch giới thiệu sản phẩm, thời gian nghỉ, ưu tiên, chống chạy trùng, pause/resume.
8. Voice Engine: TTS, voice settings, cache, queue, retry, audio routing, preview, mute/stop.
9. AI Comment Assistant: nhận comment từ nguồn được hỗ trợ hoặc nhập thủ công, normalize, deduplicate, classify intent, retrieve product facts, generate answer, moderation, approve/send nếu capability được hỗ trợ.
10. Platform Integration: TikTok adapter, Shopee adapter, OBS adapter. Mỗi adapter khai báo capabilities thực tế: stream workflow, comment read, comment send, product list, product pin, authentication, status monitoring. Unsupported capability phải hiển thị Unsupported/Requires access, không mô phỏng thành công.
11. Diagnostics: logs, health checks, export diagnostic bundle, error reporting local.
12. Demo Mode: event fixtures và simulated stream state, luôn có nhãn DEMO.

GIAO DIỆN:
Thiết kế dark-first, hiện đại, chuyên nghiệp, responsive trong cửa sổ desktop, sidebar cố định, topbar có trạng thái ứng dụng và nút quick actions. Không dùng emoji làm icon chính; dùng icon library nhất quán.
Các trang:
- Dashboard
- LIVE Studio
- Media Library
- Playlist
- Products
- Script Studio
- Automation Scheduler
- AI Comment Assistant
- Voice Studio
- Platform Connections
- Logs & Diagnostics
- Settings

Dashboard hiển thị:
- Trạng thái ứng dụng và tray.
- OBS connected/disconnected, streaming/recording, dropped frames nếu OBS cung cấp.
- TikTok và Shopee connection/capability state.
- Phiên LIVE hiện tại, thời lượng, playlist, sản phẩm hiện tại.
- AI/TTS status, queue length.
- CPU/RAM app process nếu khả thi, không gộp nhầm tài nguyên OBS.
- Các cảnh báo cần người dùng xử lý.

LIVE STUDIO:
- Preview/scene status từ OBS khi có thể.
- Start/stop/pause/resume automation, không tự start stream nếu chưa được người dùng xác nhận.
- Chọn platform profile và OBS scene.
- Chọn playlist, sản phẩm và automation schedule.
- Quick mute voice, stop speech, pause scheduler.
- Hiển thị trạng thái thật từ OBS, không chỉ trạng thái local giả định.

MEDIA:
- Import nhiều file.
- Hỗ trợ MP4/MOV/MKV/MP3/WAV tùy khả năng FFmpeg/OBS.
- Kiểm tra file tồn tại, duration, resolution, codec, audio stream.
- Thumbnail/preview nếu khả thi.
- Không đọc toàn bộ video vào RAM.
- File path được lưu an toàn, có xử lý file bị di chuyển/mất.
- Cho phép chọn video lặp, playlist tuần tự và chuyển cảnh thông qua OBS source/scene.

PRODUCTS:
- CRUD, search, filter, pagination nếu danh sách lớn.
- Trường: id, sku, name, price, currency, description, verified_features, target_audience, FAQ, policies, media references, status, createdAt, updatedAt.
- Import/export CSV có validate, preview lỗi trước khi import.
- Giá/ưu đãi phải là dữ liệu người dùng xác nhận. AI không được tự bịa giá, công dụng, tồn kho, miễn phí vận chuyển hoặc chính sách.
- Không tuyên bố sản phẩm đã ghim nếu chưa nhận được xác nhận thực tế từ tích hợp.

SCRIPT STUDIO:
- Tạo script từ product facts.
- Mẫu 15s, 30s, 60s, tùy chỉnh.
- Cho phép chỉnh sửa thủ công, lưu version, preview TTS.
- Prompt AI phải chỉ dùng dữ liệu sản phẩm được cung cấp; thiếu dữ liệu thì nói cần bổ sung.
- Có checklist duyệt script trước khi phát.

SCHEDULER:
- Lên lịch sản phẩm theo thời gian hoặc thứ tự playlist.
- Có repeat interval, cooldown, timezone, start/end time.
- Có pause/resume/skip/stop.
- Không chạy hai tác vụ giới thiệu chồng nhau.
- Persist task state để khôi phục sau restart.
- Có idempotency key và chống task chạy trùng.
- Scheduler chỉ phát lời giới thiệu; hành động ghim sản phẩm phải thông qua capability hỗ trợ hoặc hướng dẫn thao tác thủ công.

VOICE ENGINE:
- TTS provider interface: synthesize(text, voice, speed, format) -> audio asset.
- Voice settings: voice, speed, volume, language, provider.
- Preview trước khi phát LIVE.
- Cache theo hash của text + voice + speed + provider.
- Bounded audio queue, sequential playback, no overlap.
- Ưu tiên câu trả lời trực tiếp theo cấu hình; cho phép interrupt/pause scheduled speech.
- Nút global Mute, Stop current speech, Clear queue.
- Xử lý timeout, retry giới hạn, fallback provider nếu cấu hình.
- Không phát lỗi hệ thống hoặc dữ liệu nhạy cảm ra luồng LIVE.
- Kết nối audio qua OBS source/virtual audio routing được cấu hình và kiểm thử. Không giả định mọi thiết bị Windows có cùng tên.

AI COMMENT ASSISTANT:
- Comment source interface để sau này kết nối nguồn chính thức hoặc nguồn người dùng nhập.
- Comment schema gồm platform, externalId, username nếu được phép, text, timestamp, product context, processing state.
- Deduplicate bằng external id/hash.
- Intent categories: price, product_details, availability, shipping, purchase, comparison, complaint, spam, other.
- Retrieve facts từ catalog/FAQ, không cho model tự suy diễn.
- Output structured JSON: intent, answer, confidence, citedProductFacts, requiresHumanReview.
- Validate output, độ dài, moderation và rate limits.
- Manual approval là mặc định.
- Auto-send chỉ bật khi adapter khai báo commentSend=true, quyền đã được xác minh, người dùng bật rõ ràng và có rate limit.
- Nếu không có nguồn comment chính thức, cho phép nhập thủ công hoặc demo events; UI phải nói rõ không có live comment connection.
- Complaint, refund, personal data, legal/medical/financial claims, nội dung không đủ dữ liệu phải chuyển người vận hành.
- Không tự động gửi spam, không giả danh người thật hoặc tạo tương tác giả.

PLATFORM ADAPTERS:
- Define TypeScript/Rust interfaces cho TikTok, Shopee, OBS.
- Mỗi adapter có getCapabilities(), connect(), disconnect(), getStatus(), supported operations.
- TikTok: ưu tiên phương thức phát chính thức/được tài khoản hỗ trợ, OBS/RTMP khi có stream key hợp lệ, các API chỉ khi quyền được cấp. Không scrape private endpoints, không reverse-engineer auth, không lưu mật khẩu TikTok/Shopee trực tiếp.
- Shopee: hỗ trợ quy trình PC LIVE/OBS theo quyền tài khoản và hướng dẫn hiện hành. Không giả định có public API cho live comments/product pin nếu chưa xác minh.
- Không bypass CAPTCHA, OTP, login verification, anti-bot hoặc platform restrictions. Khi gặp challenge, pause và thông báo người dùng xử lý.
- Product pin/manual operation có checklist và deep link nếu URL chính thức được cấu hình; không tự tạo URL.
- Tạo docs/integration-capabilities.md nêu rõ verified, requires account access, unsupported, manual workflow.

OBS INTEGRATION:
- OBS WebSocket client, configurable host/port/password.
- Mặc định localhost, không expose WebSocket ra LAN.
- Hỗ trợ connect/disconnect, get version/status, list scenes/sources, select scene, media source controls nếu OBS API hỗ trợ.
- Không lưu OBS password plaintext; dùng Windows Credential Manager hoặc cơ chế secure storage phù hợp.
- Validate OBS connection, hiển thị lỗi hướng dẫn bật WebSocket.
- Không tự động đổi cấu hình stream key hoặc gửi stream key tới log.
- Reconnect có exponential backoff và giới hạn.

DATABASE:
Dùng SQLite migrations có version. Tạo các bảng tối thiểu:
- app_settings
- platform_connections (chỉ metadata/secret reference, không plaintext secret)
- live_sessions
- media_assets
- playlists
- playlist_items
- products
- product_media
- product_faqs
- product_scripts
- automation_rules
- scheduled_tasks
- comment_events
- ai_responses
- voice_cache
- event_logs
- schema_migrations
Có indexes cho external ids, timestamps, state, product lookup. Dùng transaction cho các thao tác nhiều bảng. Có backup/export database và phục hồi an toàn.

SECURITY:
- Không hardcode API keys, passwords, tokens hoặc stream keys.
- Secrets dùng Windows Credential Manager/secure storage; chỉ lưu secret reference trong SQLite.
- Redact credentials trong logs và diagnostics.
- Validate input, sanitize file paths, giới hạn kích thước upload/import.
- Không chạy arbitrary shell commands từ UI.
- IPC commands có validate arguments và allowlist.
- OBS WebSocket chỉ bind localhost mặc định.
- AI requests không gửi dữ liệu nhạy cảm không cần thiết.
- Có nút disconnect/revoke local credentials.

BACKGROUND & WINDOWS:
- Minimize to tray, menu Open, Pause Automation, Mute Voice, Exit.
- Close window không đồng nghĩa Exit nếu user bật minimize-to-tray.
- Exit phải hỏi xác nhận nếu automation/OBS stream đang hoạt động; không tự kill OBS.
- Optional start with Windows, mặc định off.
- Notifications cho OBS disconnect, TTS failure, verification required, automation paused.
- Không ẩn tiến trình nhằm né phát hiện; trạng thái luôn kiểm soát được qua tray.
- Persist app preferences và restore safe state sau restart. Không tự resume phát LIVE hoặc gửi comment sau crash nếu chưa có xác nhận.

PERFORMANCE:
- Event-driven, tránh polling dày.
- Bounded channels/queues và backpressure.
- Không load toàn bộ media vào memory.
- Lazy-load trang và preview.
- TTS cache.
- Giới hạn AI concurrency và timeout.
- Cleanup temp files.
- Graceful cancellation và shutdown.
- Mục tiêu benchmark app idle RAM 50-120 MB và dashboard mở 100-220 MB, xem là target cần đo, không phải cam kết. Ghi rõ OBS/FFmpeg memory riêng.
- Thêm performance diagnostics và stress test.

ERROR HANDLING:
- Typed errors, user-friendly Vietnamese messages.
- Retry chỉ với lỗi transient, exponential backoff + jitter.
- Không retry vô hạn với auth failure, permission denied hoặc verification required.
- Log correlation id cho mỗi automation run.
- Mọi tác vụ có state pending/running/succeeded/failed/requires_user_action/cancelled.
- Chỉ ghi succeeded khi có xác nhận từ module thực hiện.

TESTING:
- Unit tests cho scheduler, deduplication, product validation, script validation, TTS cache key, capability gating.
- Integration tests cho SQLite migrations/repository, OBS adapter mock, AI/TTS provider mock.
- UI tests cho critical flows nếu setup phù hợp.
- Test offline, timeout, retry, corrupted media, missing file, OBS disconnected, provider quota, app restart.
- Demo mode có fixture, nhưng test production paths không phụ thuộc demo.
- Build Windows release và kiểm tra installer.

ACCEPTANCE CRITERIA:
1. App cài và chạy trên Windows 10/11.
2. Dashboard hiển thị trạng thái thật hoặc label demo rõ ràng.
3. Import video, tạo playlist, phát qua OBS và loop theo cấu hình.
4. CRUD sản phẩm và import/export CSV hoạt động.
5. Tạo script từ verified product facts, cho phép chỉnh sửa.
6. TTS preview và phát qua OBS audio source, không chồng tiếng.
7. Scheduler phát lời giới thiệu đúng lịch, có pause/stop/skip.
8. AI tạo câu trả lời dựa trên FAQ/catalog; manual approval mặc định.
9. Unsupported platform capabilities được thể hiện rõ, không giả lập thành công.
10. Minimize-to-tray hoạt động, có mute/pause/exit.
11. Logs không chứa secrets.
12. Build, lint, typecheck, tests đạt; có README hướng dẫn cài đặt và cấu hình.

DELIVERY ORDER:
Phase 0: Inspect repo/environment, write architecture and capability assumptions.
Phase 1: Initialize app, design system, navigation, settings, SQLite migrations, tray.
Phase 2: Media library + OBS integration + playlist playback.
Phase 3: Product catalog + script studio + scheduler.
Phase 4: TTS provider + cache + audio queue + OBS audio routing.
Phase 5: AI comment assistant + moderation + manual approval + demo events.
Phase 6: TikTok/Shopee capability adapters and documented supported workflows.
Phase 7: diagnostics, security review, performance testing, packaging, README.

At each phase:
- Implement actual working code.
- Run relevant tests, lint, typecheck and build.
- Fix errors before proceeding.
- Update docs and report changed files, commands run, test results, known limitations and next phase.
- Do not stop after producing a plan. Continue implementation until all feasible phases are complete. For unavailable external credentials or platform permissions, complete the adapter boundary, UI states, tests, setup guide and clearly document the exact blocker.

Begin now by inspecting the project directory and environment, then execute Phase 0 and Phase 1. Continue through the remaining phases autonomously as far as the environment allows.