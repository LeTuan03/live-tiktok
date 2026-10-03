"""Service-layer tests: scripts, CSV, TTS cache, settings/secrets, media validation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.services.product_service import ProductService
from app.services.script_service import ScriptService
from app.services.settings_service import KEY_GEMINI_API_KEY, KEYRING_MARKER, SettingsService
from app.services.voice_service import VoiceService


# ----------------------------------------------------------------- scripts
def test_script_prompt_uses_product_data_and_guardrails(session):
    product = ProductRepository().create({"name": "Son Thỏi Matte", "price": 250000, "description": "Son lì không trôi"})
    service = ScriptService(ScriptRepository(session))
    sys_prompt, user_prompt = service.build_script_prompt(product=product, style="consultant", honorific="Chị em", duration_minutes=1.5)
    assert "Son Thỏi Matte" in user_prompt
    assert "250,000 VNĐ" in user_prompt
    assert "Chị em" in user_prompt
    assert "KHÔNG bịa" in sys_prompt  # anti-fabrication rule present


def test_script_generation_saves_script_and_adhoc_product(session):
    service = ScriptService(ScriptRepository(session))
    provider = AsyncMock()
    provider.generate_text.return_value = "Xin chào các tình yêu! Hôm nay em mang đến deal cực tốt cho kem dưỡng da..."
    script = asyncio.run(
        service.generate_script_with_ai(provider=provider, product_name="Kem Dưỡng Da", price=320000, promo_price=249000, style="friendly")
    )
    assert script.id is not None and "Kem Dưỡng Da" in script.title
    assert script.product_id is not None  # ad-hoc product created
    provider.generate_text.assert_awaited_once()


def test_script_generation_rejects_empty_ai_output(session):
    service = ScriptService(ScriptRepository(session))
    provider = AsyncMock()
    provider.generate_text.return_value = "ok"
    with pytest.raises(RuntimeError):
        asyncio.run(service.generate_script_with_ai(provider=provider, product_name="X"))


# --------------------------------------------------------------------- CSV
def test_csv_import_export_roundtrip(db, tmp_path):
    service = ProductService()
    csv_file = tmp_path / "in.csv"
    csv_file.write_text(
        "Tên sản phẩm,Mã SKU,Giá bán,Giá khuyến mãi,Trạng thái\n"
        "Áo thun,AT1,199000,149000,Có\n"
        ",AT2,1,1,Có\n"  # missing name → error row
        "Quần jean,QJ1,abc,,Không\n",  # bad price → error row
        encoding="utf-8-sig",
    )
    count, errors = service.import_csv(csv_file)
    assert count == 1
    assert len(errors) == 2
    out = tmp_path / "out.csv"
    assert service.export_csv(out) == 1
    text = out.read_text(encoding="utf-8-sig")
    assert "Áo thun" in text and "AT1" in text


def test_csv_import_rejects_missing_file(db, tmp_path):
    count, errors = ProductService().import_csv(tmp_path / "nope.csv")
    assert count == 0 and errors


# --------------------------------------------------------------------- TTS
def test_tts_cache_skips_regeneration(session, tmp_path):
    product = ProductRepository().create({"name": "P"})
    script = ScriptRepository(session).create(product_id=product.id, title="t", content="Xin chào cả nhà")
    service = VoiceService(VoiceRepository(session), storage_dir=tmp_path)

    async def fake_synth(text, output_path, voice_id, speed, volume):
        Path(output_path).write_bytes(b"\xff\xfb" * 3000)

    with patch.object(service, "_synthesize", side_effect=fake_synth) as synth:
        a1 = asyncio.run(service.generate_speech(script.id, "Xin chào cả nhà"))
        a2 = asyncio.run(service.generate_speech(script.id, "Xin chào cả nhà"))
        assert synth.await_count == 1  # second call served from cache
        assert a1.id == a2.id and a1.duration_seconds and a1.duration_seconds > 0
        a3 = asyncio.run(service.generate_speech(script.id, "Nội dung khác"))
        assert synth.await_count == 2 and a3.id != a1.id


def test_tts_hash_depends_on_voice_and_speed(session):
    service = VoiceService(VoiceRepository(session))
    h1 = service.compute_audio_hash("Xin chào", "vi-VN-HoaiMyNeural", 1.0)
    assert h1 == service.compute_audio_hash("Xin chào", "vi-VN-HoaiMyNeural", 1.0)
    assert h1 != service.compute_audio_hash("Xin chào", "vi-VN-NamMinhNeural", 1.0)
    assert h1 != service.compute_audio_hash("Xin chào", "vi-VN-HoaiMyNeural", 1.2)


def test_tts_rejects_empty_text(session):
    from app.core.exceptions import TTSProviderError

    service = VoiceService(VoiceRepository(session))
    with pytest.raises(TTSProviderError):
        asyncio.run(service.generate_speech(1, "   "))


def test_voice_service_synthesize_speech_and_duration(session, tmp_path):
    service = VoiceService(VoiceRepository(session), storage_dir=tmp_path)
    target_file = tmp_path / "test_out.mp3"

    async def fake_synth(text, output_path, voice_id, speed, volume):
        Path(output_path).write_bytes(b"\xff\xfb" * 3000)

    with patch.object(service, "_synthesize", side_effect=fake_synth):
        out_path = asyncio.run(service.synthesize_speech("Chào mừng bạn đến với livestream", output_path=target_file))
        assert Path(out_path).exists()
        assert Path(out_path) == target_file

        duration = service.get_audio_duration(out_path)
        assert duration > 0.0


def test_avatar_service_generate_speech_for_pitch(session, tmp_path):
    from app.services.avatar_service import AvatarService

    service = AvatarService(session)

    async def fake_synthesize(text, output_path, voice_id, speed, volume):
        Path(output_path).write_bytes(b"\xff\xfb" * 3000)

    with patch.object(service.voice_service, "_synthesize", side_effect=fake_synthesize):
        audio_path, duration = asyncio.run(
            service.generate_speech_for_pitch(
                text="Đây là sản phẩm áo thun cao cấp",
                voice_id="vi-VN-HoaiMyNeural",
                speed=1.0,
            )
        )
        assert audio_path is not None
        assert Path(audio_path).exists()
        assert duration > 0.0


# ---------------------------------------------------------- settings/secrets
def test_secrets_go_to_keyring_not_database(session, _fake_keyring):
    svc = SettingsService(session)
    svc.set_setting(KEY_GEMINI_API_KEY, "AIzaSyREALKEY1234567890", is_secret=True)
    from app.database.models.settings import AppSetting
    from sqlalchemy import select

    row = session.scalars(select(AppSetting).where(AppSetting.setting_key == KEY_GEMINI_API_KEY)).first()
    assert row.setting_value == KEYRING_MARKER  # never plaintext in DB
    assert svc.get_setting(KEY_GEMINI_API_KEY) == "AIzaSyREALKEY1234567890"
    assert svc.is_llm_configured()


def test_secrets_fall_back_to_database_when_keyring_missing(session, monkeypatch):
    from app.core import secrets

    monkeypatch.setattr(secrets, "keyring_available", lambda: False)
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: False)
    monkeypatch.setattr(secrets, "get_secret", lambda k: None)
    svc = SettingsService(session)
    svc.set_setting("obs_password", "pw", is_secret=True)
    assert svc.get_setting("obs_password") == "pw"


def test_settings_service_obs_getters_and_compat(session):
    svc = SettingsService(session)
    svc.set_setting("obs_host", "127.0.0.1")
    svc.set_setting("obs_port", "4455")
    svc.set_setting("obs_password", "secret_pwd", is_secret=True)
    svc.set_setting("obs_scene_name", "Main Scene")
    svc.set_setting("obs_video_source", "Main Video")
    svc.set_setting("obs_audio_source", "Main Audio")
    svc.set_setting("obs_avatar_source", "Custom Avatar")

    assert svc.get_obs_host() == "127.0.0.1"
    assert svc.get_obs_port() == 4455
    assert svc.get_obs_password() == "secret_pwd"
    assert svc.get_obs_scene() == "Main Scene"
    assert svc.get_obs_video_source() == "Main Video"
    assert svc.get_obs_audio_source() == "Main Audio"
    assert svc.get_obs_avatar_source() == "Custom Avatar"
    assert svc.get("obs_host") == "127.0.0.1"

    svc.set_secret("test_secret", "top_secret_val")
    assert svc.get_secret("test_secret") == "top_secret_val"


def test_llm_factory_requires_credentials(session):
    from app.core.exceptions import AIProviderNotConfiguredError
    from app.integrations.llm.factory import get_llm_provider, is_llm_configured
    from app.integrations.llm.gemini_provider import GeminiLLMProvider
    from app.integrations.llm.openai_provider import OpenAILLMProvider

    svc = SettingsService(session)
    assert not is_llm_configured(svc)
    with pytest.raises(AIProviderNotConfiguredError):
        get_llm_provider(svc)
    svc.set_setting("llm_provider", "gemini")
    svc.set_setting("gemini_api_key", "AIzaSyDummyKeyForTestingPurposes123456789")
    svc.set_setting("gemini_model", "gemini-2.5-flash")
    p = get_llm_provider(svc)
    assert isinstance(p, GeminiLLMProvider) and p.model_name == "gemini-2.5-flash"
    svc.set_setting("llm_provider", "openai")
    svc.set_setting("openai_base_url", "http://localhost:11434/v1")
    svc.set_setting("openai_model", "llama3")
    p = get_llm_provider(svc)
    assert isinstance(p, OpenAILLMProvider) and p.model_name == "llama3"


def test_gemini_key_validation():
    from app.integrations.llm.gemini_provider import GeminiLLMProvider

    with pytest.raises(ValueError):
        GeminiLLMProvider(api_key="")._validate_api_key()
    with pytest.raises(ValueError):
        GeminiLLMProvider(api_key="...avVQ")._validate_api_key()
    GeminiLLMProvider(api_key="AIzaSyDummyKeyForTestingPurposes123456789")._validate_api_key()


# ------------------------------------------------------------------- media
def test_media_import_validates_files(db, tmp_path):
    from app.services.media_service import MediaService

    service = MediaService()
    ok, errors = service.import_files([tmp_path / "missing.mp4"])
    assert ok == 0 and "không tồn tại" in errors[0].lower()
    bad = tmp_path / "doc.txt"
    bad.write_text("x")
    ok, errors = service.import_files([bad])
    assert ok == 0 and "không được hỗ trợ" in errors[0].lower()


def test_media_import_image_uses_pillow(db, tmp_path):
    from PIL import Image

    from app.services.media_service import MediaService

    img = tmp_path / "banner.png"
    Image.new("RGB", (1080, 1920), "purple").save(img)
    service = MediaService()
    media = service.import_file(img)
    assert media.media_type == "image" and media.width == 1080 and media.height == 1920 and media.is_valid
    product = ProductRepository().create({"name": "Cam"})
    service.link_to_product(media.id, product.id, is_primary=True)
    assert service.get_primary_image_for_product(product.id).id == media.id
    assert service.get_product_ids_for_media(media.id) == [product.id]


def test_media_missing_file_detection(db, tmp_path):
    from PIL import Image

    from app.services.media_service import MediaService

    img = tmp_path / "x.png"
    Image.new("RGB", (10, 10)).save(img)
    service = MediaService()
    media = service.import_file(img)
    img.unlink()
    missing = service.check_missing_files()
    assert [m["id"] for m in missing] == [media.id]
    assert service.get_media(media.id).is_valid is False
