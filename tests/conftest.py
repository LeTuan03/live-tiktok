"""Shared pytest fixtures — isolated data dir, in-memory DB, no real keyring."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Must be set before app.config.settings is imported anywhere.
_TMP_DATA = Path(tempfile.mkdtemp(prefix="liveflow_test_"))
os.environ["LIVEFLOW_DATA_DIR"] = str(_TMP_DATA)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Empty env vars override any key present in a developer's .env file.
os.environ["LIVEFLOW_GEMINI_API_KEY"] = ""
os.environ["LIVEFLOW_OPENAI_API_KEY"] = ""
os.environ["LIVEFLOW_OPENAI_BASE_URL"] = ""
os.environ["LIVEFLOW_LLM_PROVIDER"] = "gemini"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)


@pytest.fixture(autouse=True)
def _fake_keyring(monkeypatch):
    """Never touch the real Windows Credential Manager during tests."""
    from app.core import secrets

    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "keyring_available", lambda: True)
    monkeypatch.setattr(secrets, "set_secret", lambda k, v: (store.__setitem__(k, v) or True) if v else (store.pop(k, None) or True))
    monkeypatch.setattr(secrets, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "delete_secret", lambda k: store.pop(k, None))
    yield store


@pytest.fixture
def db():
    """Fresh in-memory database per test."""
    from app.database.session import close_database, init_database

    init_database("sqlite:///:memory:")
    yield
    close_database()


@pytest.fixture
def session(db):
    from app.database.session import get_session

    s = get_session()
    yield s
    s.close()


@pytest.fixture
def tmp_data_dir():
    return _TMP_DATA
