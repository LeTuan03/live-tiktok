"""SoShop-namespaced entrypoint for the LiveKit agent worker.

Re-exports :func:`soshop.agent.main.main` so the deployable command is
``python -m soshop_server.agent.main``. Keeping this thin seam in
``soshop_server`` gives deploy manifests (Dockerfile, livekit.toml, CI) a
single stable entrypoint, independent of how the ``soshop.*``
implementation package is organized. If ``soshop/`` is later folded into
``soshop_server``, this module's body collapses to the real
implementation and the entrypoint stays put.
"""

from __future__ import annotations

from soshop.agent.main import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
