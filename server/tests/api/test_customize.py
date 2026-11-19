"""Hermetic coverage for the public ``/customize`` routes.

No external services: the DB connection, LiveKit client, S3 upload, and the
ElevenLabs voices fetch are all faked / monkeypatched. Mirrors the fixture
style in ``test_feed_deprecated.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.agent import runner as agent_runner
from soshop.api.routes import customize as customize_routes
from soshop.api.routes.customize import router as customize_router
from soshop.livekit.client import get_livekit_api
from soshop.services import customize as customize_service
from soshop_server.api.deps import get_db_conn

# A clean 37-word script — the band target, so it always validates.
VALID_SCRIPT = (
    "Meet the cordless vacuum that finally fits under the couch and still "
    "clears pet hair in one pass. Forty minute runtime, washable filter, "
    "empties with a button. I stopped dreading the stairs entirely. Tap the "
    "card below to bring one home today."
)


def _word_count(text: str) -> int:
    return len(text.split())


def test_valid_script_is_in_band() -> None:
    # Guard the fixture itself so a future edit can't silently drift it.
    assert 30 <= _word_count(VALID_SCRIPT) <= 45


def test_persona_prompt_delivers_script_without_product() -> None:
    """A customize host dispatches with a script but no catalog product.

    Regression guard: the persona template must treat a present script as
    the pitch even when product context is ``none``. The template used to
    force freestyle mode on a missing product ("operate in freestyle mode
    regardless of whether a script was provided"), so the avatar connected
    but never delivered the user's script.
    """
    template = agent_runner._PERSONA_TEMPLATE_PATH.read_text(encoding="utf-8")
    body = agent_runner._strip_template_header(template)
    rendered = body.format(
        name="My AI Host",
        backstory="none",
        traits="none",
        voice_tone="Warm and upbeat",
        product_context=agent_runner._format_product_context(None),  # renders as "none"
        current_script=VALID_SCRIPT,
    )
    collapsed = " ".join(rendered.replace("**", "").split())

    # The script is present as the delivery target.
    assert VALID_SCRIPT in rendered
    # A missing product must NOT force freestyle while a script exists.
    assert "freestyle mode regardless" not in rendered
    assert "does not put you in freestyle mode" in collapsed
    assert "Freestyle mode is driven by the script alone" in collapsed
    # The opening directive no longer assumes a catalog product exists.
    assert "for the product" not in agent_runner.OPENING_LINE_INSTRUCTIONS
    assert "current script" in agent_runner.OPENING_LINE_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Module-global hygiene — reset the rate-limit log per test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    customize_routes._rate_log.clear()
    yield
    customize_routes._rate_log.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTxn:
    async def __aenter__(self) -> _FakeTxn:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeConn:
    def transaction(self) -> _FakeTxn:
        return _FakeTxn()

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO personas" in query:
            return {"id": uuid4()}
        if "INSERT INTO persona_scripts" in query:
            return {"id": uuid4()}
        return None

    async def execute(self, query: str, *args: Any) -> str:
        return "DELETE 0"


class _FakeAgentDispatch:
    async def create_dispatch(self, request: Any) -> Any:
        dispatch = MagicMock()
        dispatch.id = "AD_customize_test"
        return dispatch


class _FakeLk:
    def __init__(self) -> None:
        self.agent_dispatch = _FakeAgentDispatch()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.include_router(customize_router)

    async def override_db() -> AsyncIterator[_FakeConn]:
        yield _FakeConn()

    fastapi_app.dependency_overrides[get_db_conn] = override_db
    fastapi_app.dependency_overrides[get_livekit_api] = lambda: _FakeLk()

    async def fake_upload(**kwargs: Any) -> str:
        return "https://cdn.example.test/avatars/fake.png"

    # The route imports `upload_bytes` into its own namespace.
    monkeypatch.setattr(customize_routes, "upload_bytes", fake_upload)
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _post_host(client: TestClient, *, script: str, content_type: str = "image/png") -> Any:
    return client.post(
        "/customize/host",
        data={"voice_id": "voice_abc", "personality": "Warm and upbeat.", "script": script},
        files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nfake-bytes", content_type)},
    )


# ---------------------------------------------------------------------------
# validate_script (pure)
# ---------------------------------------------------------------------------


def test_validate_script_accepts_in_band() -> None:
    assert customize_service.validate_script(VALID_SCRIPT) == _word_count(VALID_SCRIPT)


def test_validate_script_rejects_too_short() -> None:
    with pytest.raises(customize_service.ScriptOutOfBandError) as excinfo:
        customize_service.validate_script("Too short to pitch anything real.")
    assert excinfo.value.count == 6


# ---------------------------------------------------------------------------
# GET /customize/voices — the curated host voices (committed seed)
# ---------------------------------------------------------------------------


def test_get_voices_returns_the_curated_eight(client: TestClient) -> None:
    # Static, local read of the committed seed — no network.
    resp = client.get("/customize/voices")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    voices = body["voices"]
    assert len(voices) == 8
    # The seed is 4 male then 4 female; the first is "Hale".
    assert voices[0]["gender"] == "male"
    assert voices[0]["name"] == "Hale"
    for v in voices:
        assert v["voice_id"]
        assert v["public_owner_id"]
        assert v["preview_url"]
        assert v["gender"] in {"male", "female"}
    genders = [v["gender"] for v in voices]
    assert genders.count("male") == 4
    assert genders.count("female") == 4


# ---------------------------------------------------------------------------
# POST /customize/host
# ---------------------------------------------------------------------------


def test_create_host_happy_path(client: TestClient) -> None:
    resp = _post_host(client, script=VALID_SCRIPT)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["room_name"].startswith("customize-")
    assert body["token"]
    assert body["livekit_url"]
    assert body["persona_id"]
    assert body["session_id"]


def test_create_host_rejects_bad_content_type(client: TestClient) -> None:
    resp = client.post(
        "/customize/host",
        data={"voice_id": "v", "personality": "Warm.", "script": VALID_SCRIPT},
        files={"avatar": ("note.txt", b"not an image", "text/plain")},
    )
    assert resp.status_code == 400, resp.text


def test_create_host_rejects_out_of_band_script(client: TestClient) -> None:
    resp = _post_host(client, script="Way too short.")
    assert resp.status_code == 422, resp.text


def test_create_host_rate_limited_after_max(client: TestClient) -> None:
    last = None
    for _ in range(customize_routes._RATE_MAX + 1):
        last = _post_host(client, script=VALID_SCRIPT)
    assert last is not None
    assert last.status_code == 429, last.text


# ---------------------------------------------------------------------------
# ensure_voice_in_account — copy a Library voice into our account
# ---------------------------------------------------------------------------


async def test_ensure_voice_passthrough_without_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    # A premade voice (no public_owner_id) returns unchanged; neither helper runs.
    async def boom_check(_voice_id: str) -> bool:  # pragma: no cover - must not run
        raise AssertionError("must not check the account for a premade voice")

    async def boom_add(*_: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("must not add a premade voice")

    monkeypatch.setattr(customize_service, "_voice_in_account", boom_check)
    monkeypatch.setattr(customize_service, "_add_shared_voice", boom_add)
    assert await customize_service.ensure_voice_in_account("premade_1", None) == "premade_1"


async def test_ensure_voice_adds_when_absent_keeping_id(monkeypatch: pytest.MonkeyPatch) -> None:
    added: dict[str, Any] = {}

    async def fake_check(_voice_id: str) -> bool:
        return False  # not yet resident -> must add

    async def fake_add(public_owner_id: str, voice_id: str, new_name: str) -> None:
        added.update(owner=public_owner_id, voice=voice_id, name=new_name)

    monkeypatch.setattr(customize_service, "_voice_in_account", fake_check)
    monkeypatch.setattr(customize_service, "_add_shared_voice", fake_add)

    # Adding a shared voice keeps its id, so the input id is returned unchanged.
    result = await customize_service.ensure_voice_in_account("lib_42", "owner_7")
    assert result == "lib_42"
    assert added == {"owner": "owner_7", "voice": "lib_42", "name": "lib_42"}


async def test_ensure_voice_skips_add_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(_voice_id: str) -> bool:
        return True  # already resident -> no add

    async def boom_add(*_: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("should not add a voice already in the account")

    monkeypatch.setattr(customize_service, "_voice_in_account", fake_check)
    monkeypatch.setattr(customize_service, "_add_shared_voice", boom_add)

    assert await customize_service.ensure_voice_in_account("lib_42", "owner_7") == "lib_42"


async def test_ensure_voice_degrades_to_source_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_check(_voice_id: str) -> bool:
        raise customize_service.VoicesUnavailableError("eleven down")

    monkeypatch.setattr(customize_service, "_voice_in_account", fake_check)
    # Degrades to the source id rather than blocking host creation (no raise).
    assert await customize_service.ensure_voice_in_account("lib_42", "owner_7") == "lib_42"


def test_create_host_copies_library_voice_before_dispatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_ensure(voice_id: str, public_owner_id: str | None) -> str:
        seen["ensure_args"] = (voice_id, public_owner_id)
        return "owned_final"

    original_insert = customize_service.insert_draft_host

    async def spy_insert(conn: Any, **kwargs: Any) -> Any:
        seen["insert_voice_id"] = kwargs["voice_id"]
        return await original_insert(conn, **kwargs)

    monkeypatch.setattr(customize_service, "ensure_voice_in_account", fake_ensure)
    monkeypatch.setattr(customize_service, "insert_draft_host", spy_insert)

    resp = client.post(
        "/customize/host",
        data={
            "voice_id": "lib_42",
            "public_owner_id": "owner_7",
            "personality": "Warm and upbeat.",
            "script": VALID_SCRIPT,
        },
        files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nfake-bytes", "image/png")},
    )
    assert resp.status_code == 201, resp.text
    # The route resolves the picked Library voice to an owned copy, then
    # persists that owned id on the draft persona.
    assert seen["ensure_args"] == ("lib_42", "owner_7")
    assert seen["insert_voice_id"] == "owned_final"
