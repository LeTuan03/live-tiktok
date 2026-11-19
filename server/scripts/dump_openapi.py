"""Dump the FastAPI app's OpenAPI schema to ``server/openapi.json``.

Hermetic — does not touch Postgres / LiveKit. We just import
the app, call ``app.openapi()`` (FastAPI's already-cached
introspection), and write the result to disk. The web subsystem reads
this file via ``@hey-api/openapi-ts`` to generate a typed TS SDK; see
``web/openapi-ts.config.ts``.

Run it directly::

    uv run python scripts/dump_openapi.py

…or via the web codegen pipeline::

    cd web && npm run gen:api
"""

from __future__ import annotations

import json
from pathlib import Path

from soshop_server.api.app import app


def main() -> None:
    schema = app.openapi()
    out = Path(__file__).resolve().parent.parent / "openapi.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
