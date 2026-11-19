"""Preload the curated host voices into our ElevenLabs account.

Reads ``seed/curated_voices.json`` and copies each Voice Library source voice
into our account via :func:`soshop.services.customize.ensure_voice_in_account`
(idempotent: a voice already copied is matched by its ``soshop-lib-<id>`` marker
and reused). This warms the account so the ``/customize`` curated picker and the
agent's dispatch-time copy both resolve to a stable, owned voice_id.

Run from server/ with ELEVEN_API_KEY configured:

    uv run python scripts/preload_curated_voices.py

Idempotent and safe to re-run (e.g. after editing the curated lineup or pointing
at a new ElevenLabs account). Exits non-zero if any voice could not be copied.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from soshop.services.customize import _voice_in_account, ensure_voice_in_account
from soshop_server.core.config import settings

_SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "curated_voices.json"


async def main() -> int:
    if not settings.ELEVEN_API_KEY:
        print("ELEVEN_API_KEY is not configured; aborting.", file=sys.stderr)
        return 2

    voices = json.loads(_SEED_PATH.read_text(encoding="utf-8"))["voices"]
    failures = 0
    for v in voices:
        vid = v["voice_id"]
        # Idempotent: adds the voice only if it isn't already resident. The id is
        # unchanged; we confirm by re-checking library presence.
        await ensure_voice_in_account(vid, v.get("public_owner_id"), name=v["display_name"])
        present = await _voice_in_account(vid)
        print(f"{'ok    ' if present else 'FAILED'}  {v['display_name']:10} {vid}")
        failures += 0 if present else 1

    print(f"\n{len(voices) - failures}/{len(voices)} curated voices present in the account.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
