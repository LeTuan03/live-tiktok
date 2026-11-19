"""Seed-data gate: each host has at least 10 pitches, each with a real SKU.

Operator-facing contract: the channel rotation cycles ``ads`` rows
filtered by ``persona_id`` (see
``soshop.agent.channel_loop._load_active_ads``). If a persona's
seed JSON has fewer than ten scripts with valid SKUs, that channel's
rotation will feel repetitive within the first 30s of viewing.
``rebuild_ads`` in ``server/scripts/seed_v1.py`` silently drops
scripts without a ``product_sku`` or whose SKU isn't in
``seed_v1.PRODUCTS``, so a typo in the JSON could collapse a rotation
back to its previous size without any other test catching it.

This file is the load-bearing gate. Hermetic — no DB.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PER_HOST_FLOOR = 10
"""Each host's rotation must cover at least this many pitches.

Bumping the floor is one-line; the rotation cursor itself wraps via
modulo so larger N is always safe. ``rebuild_ads`` writes one ad per
(persona × script-with-SKU) so the floor maps 1:1 to the JSON's
script count.
"""

_SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "personas"
_SEED_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_v1.py"


def _load_seed_module():
    """Load ``server/scripts/seed_v1.py`` as a module for SKU lookup.

    The script lives outside the ``soshop_server`` package and runs
    via ``uv run python scripts/seed_v1.py``, so it isn't importable
    by name. We load it as a one-off module here so the test reads
    the exact ``PRODUCTS`` list the seed will write, not a
    hand-mirrored copy.
    """
    spec = importlib.util.spec_from_file_location("_seed_v1_for_tests", _SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_seed_v1_for_tests"] = module
    spec.loader.exec_module(module)
    return module


def _persona_files() -> list[Path]:
    files = sorted(_SEED_DIR.glob("*.json"))
    if not files:
        # Fresh clone: only the committed `.json.example` template exists.
        files = sorted(_SEED_DIR.glob("*.json.example"))
    return files


def test_each_persona_has_at_least_ten_scripts() -> None:
    """Every host's rotation covers the per-host floor."""
    files = _persona_files()
    assert files, f"no persona JSONs under {_SEED_DIR}"
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        scripts = data.get("scripts", [])
        slug = data["slug"]
        assert len(scripts) >= _PER_HOST_FLOOR, (
            f"{slug} has only {len(scripts)} scripts; rotation needs >= "
            f"{_PER_HOST_FLOOR} so the channel doesn't repeat within a "
            "short viewing session"
        )


@pytest.fixture(scope="module")
def known_skus() -> set[str]:
    module = _load_seed_module()
    products = getattr(module, "PRODUCTS", None)
    assert products is not None, "seed_v1 has no PRODUCTS list"
    return {p["sku"] for p in products}


@pytest.mark.parametrize("persona_path", _persona_files(), ids=lambda p: p.stem)
def test_every_script_has_a_real_sku(persona_path: Path, known_skus: set[str]) -> None:
    """Each script's ``product_sku`` must resolve in ``seed_v1.PRODUCTS``.

    ``rebuild_ads`` silently drops scripts whose SKU isn't in the
    products list — a typo would shrink the rotation without any
    other signal. Catching it here keeps the rotation invariant
    enforced at author time.
    """
    data = json.loads(persona_path.read_text(encoding="utf-8"))
    slug = data["slug"]
    for idx, script in enumerate(data.get("scripts", [])):
        sku = script.get("product_sku")
        assert sku, f"{slug}[{idx}] has no product_sku (rotation would skip it)"
        assert sku in known_skus, (
            f"{slug}[{idx}] references SKU {sku!r} which is not in seed_v1.PRODUCTS — "
            "rebuild_ads would silently drop this script"
        )
