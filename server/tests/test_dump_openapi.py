"""Smoke test for `scripts/dump_openapi.py`.

The script is the codegen seam for the web SDK (`cd web && npm run
gen:api` invokes it). It must:

  - Import the FastAPI app without touching Postgres / LiveKit.
  - Produce a parseable JSON document at `server/openapi.json`.
  - Include at least one path so the web side gets something to mirror.

Tests delete the generated file in `finally` so a hermetic run leaves
the tree clean (the file is gitignored but we still don't want stray
artifacts between local runs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
_OPENAPI_JSON = _REPO_ROOT / "openapi.json"


@pytest.fixture
def restore_openapi_json():
    """Snapshot the openapi.json (if any) so the test never clobbers it."""
    original = _OPENAPI_JSON.read_bytes() if _OPENAPI_JSON.exists() else None
    try:
        yield
    finally:
        if original is None:
            _OPENAPI_JSON.unlink(missing_ok=True)
        else:
            _OPENAPI_JSON.write_bytes(original)


def test_dump_openapi_writes_parseable_spec(restore_openapi_json):
    # Run the script's `main()` in-process rather than spawning uv so the
    # test stays inside the pytest event loop and reuses the already-
    # imported app. (The script is intentionally short — no orchestration
    # beyond `app.openapi() → json.dump`.)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import dump_openapi  # noqa: PLC0415

        dump_openapi.main()
    finally:
        sys.path.remove(str(_SCRIPTS))

    assert _OPENAPI_JSON.exists()
    data = json.loads(_OPENAPI_JSON.read_text())
    assert data["openapi"].startswith("3.")
    assert data["info"]["title"] == "SoShop Server API"
    # Sanity: at least the public healthz route is mounted.
    assert "/healthz" in data["paths"]
    # And the consumer-facing channels route the clients depend on.
    assert "/channels" in data["paths"]
