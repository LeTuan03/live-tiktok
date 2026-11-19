"""Local, keyless talking avatar: a looping motion clip with realtime lip sync.

See ``README.md`` in this package for the design, models and limits.
"""

from __future__ import annotations

from pathlib import Path

from soshop.agent.lipsync.assets import assets_ready, read_status
from soshop.agent.lipsync.avatar import LipSyncAvatar

__all__ = ["LipSyncAvatar", "avatar_assets_ready", "read_status"]


def avatar_assets_ready(assets_dir: Path) -> bool:
    """True when ``assets_dir`` holds a finished build the runtime can load."""
    return assets_ready(assets_dir)
