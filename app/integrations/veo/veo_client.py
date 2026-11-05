"""Google Veo 3 / 3.1 API Client for AI Video Generation.

Communicates with Google Generative Language API (:predictLongRunning)
to generate high-fidelity videos from text prompts and source reference images.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import aiofiles
import aiohttp
from loguru import logger

from app.config.settings import get_settings

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class VeoClient:
    """Client for Google Veo 3 / 3.1 video generation API."""

    DEFAULT_MODEL = "gemini-omni-1.1-flash"
    FALLBACK_MODEL = ""
    OMNI_PREFIX = "gemini-omni"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: int = 360,
    ) -> None:
        settings = get_settings()
        self.api_key = self._resolve_api_key(api_key)
        self.model_name = (model_name or settings.veo_model or self.DEFAULT_MODEL).strip()
        self.fallback_model = (settings.veo_fallback_model or "").strip()
        self.timeout = timeout

    @staticmethod
    def _resolve_api_key(explicit: Optional[str] = None) -> str:
        """Find the Gemini key the same way the LLM provider does.

        Order: explicit argument -> key saved in Settings (Windows Credential Manager / DB)
        -> GEMINI_API_KEY / LIVEFLOW_GEMINI_API_KEY env vars -> settings.gemini_api_key.
        """
        if explicit and explicit.strip():
            return explicit.strip()
        try:
            from app.database.session import get_db
            from app.services.settings_service import KEY_GEMINI_API_KEY, SettingsService

            with get_db() as db:
                saved = SettingsService(db).get_setting(KEY_GEMINI_API_KEY, "")
                if saved and saved.strip():
                    return saved.strip()
        except Exception as e:
            logger.debug("Không đọc được Gemini key từ Cài đặt: {}", e)
        return (
            os.getenv("GEMINI_API_KEY", "")
            or os.getenv("LIVEFLOW_GEMINI_API_KEY", "")
            or get_settings().gemini_api_key
            or ""
        ).strip()

    def _validate_api_key(self) -> None:
        if not self.api_key:
            self.api_key = self._resolve_api_key()
        if not self.api_key:
            raise ValueError(
                "Chưa cấu hình Google Gemini API Key. "
                "Vui lòng vào 'Cài đặt' hoặc thêm LIVEFLOW_GEMINI_API_KEY vào tệp .env."
            )
        if "..." in self.api_key or len(self.api_key) < 20 or " " in self.api_key:
            raise ValueError(
                "Gemini API Key không hợp lệ hoặc đang bị rút gọn. "
                "Vui lòng truy cập Google AI Studio để sao chép đầy đủ API Key."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

    DEPRECATED_MODELS = {
        "veo-3.1-fast-generate-preview",
        "veo-3.1-generate-preview",
        "veo-2.0-generate-preview",
    }

    async def preflight(self) -> tuple[bool, str]:
        """Kiểm tra trước xem có render được video AI thật không.

        Trả về (chạy_được, lý_do). Khi không chạy được, lý do viết sẵn cho người vận hành
        đọc hiểu và biết phải làm gì — thay vì để họ nhận một video giả rồi tưởng là thật.
        """
        try:
            self._validate_api_key()
        except Exception as e:
            return False, (
                f"{e}\n\n"
                "Khắc phục: mở Cài đặt → nhập Gemini API Key, hoặc thêm "
                "LIVEFLOW_GEMINI_API_KEY vào tệp .env."
            )

        url = f"{API_BASE}/models?key={self.api_key}"
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        return False, (
                            f"Không truy cập được Google AI (HTTP {resp.status}).\n\n{body}\n\n"
                            "Khắc phục: kiểm tra mạng và API Key."
                        )
                    data = await resp.json()
        except Exception as e:
            return False, (
                f"Không kết nối được tới Google AI: {e}\n\n"
                "Khắc phục: kiểm tra kết nối Internet rồi thử lại."
            )

        available = {m.get("name", "").removeprefix("models/") for m in data.get("models", [])}
        if self.model_name in available:
            return True, ""

        video_models = sorted(n for n in available if n.startswith(("veo", "gemini-omni")))
        if video_models:
            goi_y = "\n".join(f"  • {n}" for n in video_models)
            return False, (
                f"Model '{self.model_name}' không có trong tài khoản này.\n\n"
                f"Các model video key của bạn đang có:\n{goi_y}\n\n"
                "Khắc phục: đặt LIVEFLOW_VEO_MODEL trong .env thành một trong các model trên."
            )

        return False, (
            f"Tài khoản này chưa có model video nào (đang tìm '{self.model_name}').\n\n"
            + self.FREE_TIER_HELP
        )

    # Lời giải thích dùng chung cho cả preflight lẫn lỗi lúc render.
    FREE_TIER_HELP = (
        "Nguyên nhân thường gặp: API Key vẫn ở Free Tier.\n"
        "Lưu ý: gói thuê bao Google AI Pro KHÔNG mở quota cho API — đó là hai hệ thống "
        "tính tiền tách rời.\n\n"
        "Hai cách khắc phục:\n"
        "  1. Bật Billing tại https://ai.dev/rate-limit cho project của API Key, HOẶC\n"
        "  2. Dùng gói Pro tạo video người mẫu thủ công trong app Gemini rồi nạp vào "
        "LiveFlow — xem docs/HUONG_DAN_GOOGLE_PRO.md."
    )

    @classmethod
    def explain_failure(cls, error: BaseException) -> str:
        """Dịch lỗi kỹ thuật của Google thành hướng dẫn người vận hành đọc được."""
        msg = str(error)

        if "429" in msg or "too_many_requests" in msg or "Rate limit" in msg:
            if "limit: 0" in msg:
                return (
                    "Tài khoản chưa được cấp quota tạo video (giới hạn 0 lượt/ngày).\n\n"
                    + cls.FREE_TIER_HELP
                )
            return (
                "Đã chạm giới hạn tốc độ của Google AI. Chờ ít phút rồi thử lại, "
                "hoặc nâng tier tại https://ai.dev/rate-limit."
            )

        if "404" in msg or "not found" in msg.lower():
            return (
                "Model video đang cấu hình không tồn tại với API Key này.\n\n"
                + cls.FREE_TIER_HELP
            )

        if "401" in msg or "403" in msg or "API key" in msg or "PERMISSION" in msg.upper():
            return (
                "API Key không hợp lệ hoặc không có quyền.\n\n"
                "Khắc phục: mở Cài đặt → nhập lại Gemini API Key (lấy tại "
                "https://aistudio.google.com/app/apikey)."
            )

        return msg

    async def generate_video(
        self,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[str | Path] = None,
        mime_type: str = "image/jpeg",
        aspect_ratio: str = "9:16",
        duration_seconds: int = 5,
        resolution: str = "720p",
        output_path: Optional[str | Path] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        audio_path: Optional[str | Path] = None,
        shot_type: str = "HOOK",
    ) -> str:
        """Generate a video clip using Google Gemini Omni / Veo or Studio AI Motion & Lip-Sync Engine.

        Args:
            prompt: Text prompt describing the video scene, movement, camera angle.
            image_bytes: Raw bytes of the starting/reference image (optional).
            image_path: Path to reference image on disk (used if image_bytes not given).
            mime_type: Mime type of the image (image/jpeg, image/png).
            aspect_ratio: '9:16' (vertical) or '16:9' (horizontal).
            duration_seconds: Target video length (5 to 8 seconds).
            resolution: '720p' or '1080p'.
            output_path: Destination path for the saved MP4 file.
            progress_callback: Callback(status_text, percent) for progress updates.
            audio_path: Optional TTS audio path for synchronized speech lip-sync.
            shot_type: 'HOOK', 'FEATURE_DEMO', or 'CTA'.

        Returns:
            Absolute path to the generated MP4 file.
        """
        allow_local = bool(get_settings().veo_allow_local_fallback)

        # Gemini Omni: real AI video from a standard Gemini API key via Interactions API
        if self.model_name.startswith(self.OMNI_PREFIX):
            try:
                return await self._generate_omni_video(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    image_path=image_path,
                    mime_type=mime_type,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    output_path=output_path,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                logger.error("Gemini Omni tạo video thất bại: {}", e)
                if not allow_local:
                    raise
                logger.warning("veo_allow_local_fallback=True -> dùng engine cục bộ (ảnh tĩnh cử động nhẹ).")
                return await self._generate_kinetic_scene_video(
                    image_path=image_path,
                    image_bytes=image_bytes,
                    duration_seconds=duration_seconds,
                    output_path=output_path,
                    progress_callback=progress_callback,
                    audio_path=audio_path,
                    shot_type=shot_type,
                )

        # Model flagged as deprecated on Google AI Studio
        if self.model_name in self.DEPRECATED_MODELS and not os.getenv("LIVEFLOW_VEO_VERTEX_ENDPOINT"):
            if not allow_local:
                raise RuntimeError(
                    f"Model '{self.model_name}' không còn khả dụng trên Google AI Studio. "
                    f"Vui lòng đặt LIVEFLOW_VEO_MODEL={self.DEFAULT_MODEL} hoặc chọn model video hợp lệ."
                )
            logger.warning(
                "Mô hình '{}' không khả dụng; veo_allow_local_fallback=True -> dùng engine cục bộ.",
                self.model_name,
            )
            if progress_callback:
                progress_callback("Kích hoạt engine cục bộ (ảnh tĩnh cử động nhẹ)...", 40)
            return await self._generate_kinetic_scene_video(
                image_path=image_path,
                image_bytes=image_bytes,
                duration_seconds=duration_seconds,
                output_path=output_path,
                progress_callback=progress_callback,
                audio_path=audio_path,
                shot_type=shot_type,
            )

        self._validate_api_key()

        if image_path and not image_bytes:
            p = Path(image_path)
            if p.exists():
                async with aiofiles.open(p, "rb") as f:
                    image_bytes = await f.read()
                if p.suffix.lower() == ".png":
                    mime_type = "image/png"
                else:
                    mime_type = "image/jpeg"

        # Try primary model, fallback if 503/404 occurs
        models_to_try = [self.model_name]
        if self.fallback_model and self.fallback_model != self.model_name:
            models_to_try.append(self.fallback_model)

        last_error = None
        for model in models_to_try:
            if model in self.DEPRECATED_MODELS:
                continue
            try:
                logger.info(
                    "Bắt đầu gọi Google Veo ({}) | aspect_ratio={} | duration={}s",
                    model,
                    aspect_ratio,
                    duration_seconds,
                )
                if progress_callback:
                    progress_callback(f"Đang gửi yêu cầu tới Veo 3 ({model})...", 10)

                return await self._execute_generation(
                    model=model,
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    resolution=resolution,
                    output_path=output_path,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                last_error = e
                err_str = str(e)
                logger.warning("Veo model {} gặp lỗi: {}", model, err_str)
                if "503" in err_str or "overloaded" in err_str.lower():
                    logger.info("Chuyển sang model dự phòng tiếp theo...")
                    continue
                if "404" in err_str or "no longer available" in err_str:
                    logger.info("Veo preview model không khả dụng, thử model tiếp theo...")
                    continue
                raise

        if not allow_local:
            raise RuntimeError(f"Không tạo được video bằng Veo: {last_error or 'không có model khả dụng'}")

        # Fallback to local AI Motion Engine (only when explicitly allowed)
        logger.warning(
            "veo_allow_local_fallback=True -> dùng engine cục bộ (ảnh tĩnh cử động nhẹ)."
        )
        if progress_callback:
            progress_callback("Studio AI Motion & Lip-Sync Engine đang render video...", 50)
        return await self._generate_kinetic_scene_video(
            image_path=image_path,
            image_bytes=image_bytes,
            duration_seconds=duration_seconds,
            output_path=output_path,
            progress_callback=progress_callback,
            audio_path=audio_path,
            shot_type=shot_type,
        )

    # ------------------------------------------------------------------
    # Gemini Omni (POST /v1beta/interactions, synchronous, URI delivery)
    # ------------------------------------------------------------------
    @classmethod
    def _find_video_item(cls, node: Any) -> Optional[Dict[str, Any]]:
        """Find the first video item that carries bytes or a URI."""
        if isinstance(node, dict):
            is_video = (
                node.get("type") == "video"
                or str(node.get("mime_type", "")).startswith("video/")
                or (isinstance(node.get("uri"), str) and ("/files/" in node["uri"] or ".mp4" in node["uri"]))
            )
            if is_video and (node.get("data") or node.get("uri")):
                return node
            for value in node.values():
                found = cls._find_video_item(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = cls._find_video_item(value)
                if found:
                    return found
        return None

    @staticmethod
    def _file_id_from_uri(uri: str) -> str:
        """Extract the Files-API id from a ``.../files/<id>:download?alt=media`` URI."""
        if "/files/" not in uri:
            return ""
        tail = uri.split("/files/", 1)[1]
        return tail.split(":", 1)[0].split("?", 1)[0].strip("/")

    async def _generate_omni_video(
        self,
        prompt: str,
        image_bytes: Optional[bytes],
        image_path: Optional[str | Path],
        mime_type: str,
        aspect_ratio: str,
        resolution: str,
        output_path: Optional[str | Path],
        progress_callback: Optional[Callable[[str, int], None]],
    ) -> str:
        """Generate a real AI video clip (with native audio) using Gemini Omni."""
        import json
        import uuid

        self._validate_api_key()

        if image_path and not image_bytes:
            p = Path(image_path)
            if p.exists():
                async with aiofiles.open(p, "rb") as f:
                    image_bytes = await f.read()
                mime_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"

        content: list[Dict[str, Any]] = []
        if image_bytes:
            content.append(
                {
                    "type": "image",
                    "data": base64.b64encode(image_bytes).decode("utf-8"),
                    "mime_type": mime_type,
                }
            )
        content.append({"type": "text", "text": prompt})

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "input": content,
            "response_format": {
                "type": "video",
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "delivery": "uri",  # videos can exceed inline limits
            },
        }

        if output_path:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = get_settings().effective_veo_dir / f"omni_{uuid.uuid4().hex[:12]}.mp4"

        if progress_callback:
            progress_callback(f"Đang gửi yêu cầu tới Gemini Omni ({self.model_name})...", 10)
        logger.info("Gemini Omni: model={} aspect={} res={}", self.model_name, aspect_ratio, resolution)

        # Call is synchronous on Google server; allow up to 360s
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=max(self.timeout, 300))
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            post_url = f"{API_BASE}/interactions"
            if "key=" not in post_url:
                post_url += f"?key={self.api_key}"
            async with session.post(post_url, headers=self._headers(), json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Gemini Omni lỗi [{resp.status}]: {text[:600]}")
                try:
                    data = json.loads(text)
                except Exception:
                    raise RuntimeError(f"Gemini Omni trả về dữ liệu không đọc được: {text[:300]}")

            item = self._find_video_item(data)
            if not item:
                raise RuntimeError(f"Gemini Omni không trả về video: {text[:600]}")

            if progress_callback:
                progress_callback("Gemini Omni đã tạo xong, đang tải video về máy...", 70)

            if item.get("data"):
                async with aiofiles.open(dest, "wb") as f:
                    await f.write(base64.b64decode(item["data"]))
            else:
                await self._download_omni_file(session, str(item["uri"]), dest)

        if not dest.exists() or dest.stat().st_size < 10_000:
            raise RuntimeError("Video Gemini Omni tải về bị rỗng hoặc hỏng.")

        if progress_callback:
            progress_callback("Tạo video Gemini Omni hoàn tất!", 100)
        logger.info("Đã lưu video Gemini Omni tại: {}", dest)
        return str(dest)

    async def _download_omni_file(self, session: aiohttp.ClientSession, uri: str, dest: Path) -> None:
        """Wait until the generated file is ACTIVE on the Files API, then download it."""
        import time

        started = time.monotonic()
        file_id = self._file_id_from_uri(uri)

        if file_id:
            while True:
                poll_url = f"{API_BASE}/files/{file_id}"
                if "key=" not in poll_url:
                    poll_url += f"?key={self.api_key}"
                async with session.get(poll_url, headers=self._headers()) as resp:
                    if resp.status == 200:
                        try:
                            info = await resp.json()
                            state = str(info.get("state", "")).upper()
                            if state in ("ACTIVE", "SUCCEEDED"):
                                break
                            if state in ("FAILED", "ERROR"):
                                raise RuntimeError("Google báo lỗi khi xử lý file video (state=FAILED).")
                        except Exception as e:
                            if "state=FAILED" in str(e):
                                raise
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError("Quá thời gian chờ file video sẵn sàng trên Google.")
                await asyncio.sleep(3)

        if file_id:
            url = f"{API_BASE}/files/{file_id}:download?alt=media"
        else:
            url = uri
            if ":download" not in url and "alt=media" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}alt=media"

        if "generativelanguage.googleapis.com" in url and "key=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={self.api_key}"

        last_status = 0
        for _ in range(6):  # allow a few retries if file is just transitioning
            async with session.get(url, headers=self._headers()) as resp:
                last_status = resp.status
                if resp.status == 200:
                    async with aiofiles.open(dest, "wb") as f:
                        while True:
                            chunk = await resp.content.read(65536)
                            if not chunk:
                                break
                            await f.write(chunk)
                    return
            await asyncio.sleep(3)
        raise RuntimeError(f"Không tải được video từ Google (HTTP {last_status}).")

    async def _execute_generation(
        self,
        model: str,
        prompt: str,
        image_bytes: Optional[bytes],
        mime_type: str,
        aspect_ratio: str,
        duration_seconds: int,
        resolution: str,
        output_path: Optional[str | Path],
        progress_callback: Optional[Callable[[str, int], None]],
    ) -> str:
        url = f"{API_BASE}/models/{model}:predictLongRunning"

        # Build instance payload
        instance: Dict[str, Any] = {"prompt": prompt}
        if image_bytes:
            b64_str = base64.b64encode(image_bytes).decode("utf-8")
            instance["image"] = {
                "bytesBase64Encoded": b64_str,
                "mimeType": mime_type,
            }

        payload = {
            "instances": [instance],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": aspect_ratio,
                "durationSeconds": int(duration_seconds),
                "resolution": resolution,
            },
        }

        # Step 1: Submit predictLongRunning
        operation_name = ""
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                status = resp.status
                text = await resp.text()

                if status != 200:
                    raise RuntimeError(f"Veo API submit error [{status}]: {text}")

                try:
                    data = await resp.json()
                except Exception:
                    import json
                    data = json.loads(text)

                operation_name = data.get("name", "")
                if not operation_name:
                    raise RuntimeError(f"Veo API không trả về operation name: {text}")

        logger.info("Veo operation khởi tạo thành công: {}", operation_name)
        if progress_callback:
            progress_callback("Veo 3 đang render video...", 20)

        # Step 2: Poll operation status
        video_uri = await self._poll_operation(operation_name, progress_callback)

        # Step 3: Download video file
        if not output_path:
            settings = get_settings()
            import uuid
            out_filename = f"veo_{uuid.uuid4().hex[:12]}.mp4"
            output_path = settings.effective_veo_dir / out_filename
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback("Đang tải video hoàn chỉnh về máy...", 90)

        await self._download_video(video_uri, output_path)

        if progress_callback:
            progress_callback("Tạo video Veo 3 hoàn tất!", 100)

        logger.info("Đã lưu video Veo tại: {}", output_path)
        return str(output_path)

    async def _poll_operation(
        self,
        operation_name: str,
        progress_callback: Optional[Callable[[str, int], None]],
    ) -> str:
        """Poll the long-running operation until complete and return video download URI."""
        poll_url = f"{API_BASE}/{operation_name}"
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=30)

        start_time = asyncio.get_event_loop().time()
        poll_interval = 8  # seconds
        max_duration = self.timeout
        percent = 25

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_duration:
                raise TimeoutError(f"Quá thời gian chờ Veo render video ({max_duration}s)")

            await asyncio.sleep(poll_interval)

            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(poll_url, headers=self._headers()) as resp:
                    status = resp.status
                    if status != 200:
                        err_text = await resp.text()
                        logger.warning("Lỗi tạm thời khi poll Veo operation [{}] : {}", status, err_text)
                        continue

                    data = await resp.json()
                    done = data.get("done", False)

                    if not done:
                        percent = min(85, percent + 5)
                        if progress_callback:
                            progress_callback(
                                f"Veo 3 đang xử lý video ({int(elapsed)}s)...", percent
                            )
                        continue

                    # Operation is complete! Check for error
                    if "error" in data:
                        err_msg = data["error"].get("message", str(data["error"]))
                        raise RuntimeError(f"Veo rendering thất bại: {err_msg}")

                    response_data = data.get("response", {})
                    # Locate video URI
                    video_uri = ""
                    # Check predictions format
                    preds = response_data.get("predictions", [])
                    if preds and isinstance(preds[0], dict):
                        v_obj = preds[0].get("video", {})
                        video_uri = v_obj.get("uri", "")

                    # Check generatedVideos format
                    if not video_uri:
                        gen_videos = response_data.get("generatedVideos", [])
                        if gen_videos and isinstance(gen_videos[0], dict):
                            v_obj = gen_videos[0].get("video", {})
                            video_uri = v_obj.get("uri", "")

                    # Check direct uri
                    if not video_uri and "videoUri" in response_data:
                        video_uri = response_data["videoUri"]

                    if not video_uri:
                        raise RuntimeError(
                            f"Không tìm thấy URL video trong kết quả Veo: {response_data}"
                        )

                    return video_uri

    async def _download_video(self, video_uri: str, dest_path: Path) -> None:
        """Download video from Veo URI to local disk."""
        connector = aiohttp.TCPConnector(ssl=False)
        # Download might require API key header or query param
        headers = self._headers()
        url = video_uri
        if "generativelanguage.googleapis.com" in url and "key=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={self.api_key}"

        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Không thể tải video từ URL ({resp.status}): {text[:200]}")

                async with aiofiles.open(dest_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(65536)
                        if not chunk:
                            break
                        await f.write(chunk)

    async def _generate_kinetic_scene_video(
        self,
        image_path: Optional[str | Path],
        image_bytes: Optional[bytes],
        duration_seconds: int,
        output_path: Optional[str | Path],
        progress_callback: Optional[Callable[[str, int], None]],
        audio_path: Optional[str | Path] = None,
        shot_type: str = "HOOK",
    ) -> str:
        """High-Fidelity AI Scene Video Generator: transforms frames into dynamic video with lip-sync and cinematic motion."""
        settings = get_settings()
        if not output_path:
            import uuid
            out_filename = f"ai_scene_{uuid.uuid4().hex[:12]}.mp4"
            output_path = settings.effective_veo_dir / out_filename
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure image file on disk
        if not image_path or not Path(image_path).exists():
            if image_bytes:
                import uuid
                tmp_img = settings.effective_veo_dir / f"tmp_{uuid.uuid4().hex[:8]}.jpg"
                async with aiofiles.open(tmp_img, "wb") as f:
                    await f.write(image_bytes)
                image_path = tmp_img
            else:
                raise ValueError("Không có hình ảnh tham chiếu để tạo video.")

        image_path = Path(image_path)
        ffmpeg_bin = settings.ffmpeg_path or "ffmpeg"

        # Determine actual target duration from audio if provided
        actual_duration = float(duration_seconds)
        if audio_path and Path(audio_path).exists():
            try:
                import json
                import subprocess
                cmd_prob = [
                    settings.ffprobe_path or "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "json",
                    str(audio_path),
                ]
                res = subprocess.run(cmd_prob, capture_output=True, text=True, check=True)
                aud_data = json.loads(res.stdout)
                dur = float(aud_data.get("format", {}).get("duration", 0.0))
                if dur > 0.5:
                    actual_duration = dur + 0.15
            except Exception:
                pass

        # Case A: Presenter scene (HOOK, CTA, INTRO) with audio -> Use AvatarAnimator for real lip-sync, blinking, and breathing
        if audio_path and Path(audio_path).exists() and shot_type in ("HOOK", "CTA", "INTRO"):
            try:
                if progress_callback:
                    progress_callback("AI Presenter Engine đang tạo khẩu hình nhép môi và cử động tự nhiên...", 65)

                from app.render.avatar_animator import AvatarAnimator
                animator = AvatarAnimator(ffmpeg_bin=ffmpeg_bin)
                # Run synchronous render in thread to avoid blocking event loop
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    animator.generate_talking_video,
                    image_path,
                    audio_path,
                    output_path,
                    30,
                )

                if progress_callback:
                    progress_callback("Tạo video phân cảnh người mẫu AI hoàn tất!", 100)
                logger.info("Đã tạo video người mẫu AI nói chuyện tại: {}", output_path)
                return str(output_path)
            except Exception as e:
                logger.warning("AvatarAnimator gặp lỗi, chuyển sang Cinematic Motion Engine: {}", e)

        # Case B: Product demo scene (FEATURE_DEMO, CLOSEUP) or fallback -> Cinematic camera motion
        total_frames = max(30, int(actual_duration * 30))

        if progress_callback:
            progress_callback("Cinematic Motion Engine đang tạo chuyển động điện ảnh mượt mà...", 65)

        # Dynamic multi-axis Ken Burns motion with subtle 3D parallax float
        filter_str = (
            f"[0:v]scale=1080:1920,"
            f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-(iw/zoom/2)+sin(in/35)*15':y='ih/2-(ih/zoom/2)+cos(in/45)*10':d={total_frames}:s=1080x1920:fps=30[v]"
        )

        cmd = [
            ffmpeg_bin,
            "-hwaccel", "none",
            "-y",
            "-loop", "1",
            "-i", str(image_path.resolve()),
        ]

        if audio_path and Path(audio_path).exists():
            cmd.extend(["-i", str(Path(audio_path).resolve())])

        cmd.extend([
            "-filter_complex", filter_str,
            "-map", "[v]",
        ])

        if audio_path and Path(audio_path).exists():
            cmd.extend([
                "-map", "1:a",
                "-c:a", "aac",
                "-b:a", "192k",
            ])

        cmd.extend([
            "-t", f"{actual_duration:.2f}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "20",
            str(output_path),
        ])

        logger.info("Chạy Cinematic Video Generator: {}", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Cinematic Motion Engine gặp lỗi ({proc.returncode}): {err[-300:]}")

        if progress_callback:
            progress_callback("Tạo video phân cảnh hoàn tất!", 100)

        logger.info("Đã tạo video phân cảnh tại: {}", output_path)
        return str(output_path)
