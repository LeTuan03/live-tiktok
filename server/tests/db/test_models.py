"""Parity check: Pydantic row models cover the columns declared in the
migration SQL.

Hermetic — parses the raw `.up.sql` text and compares column names against
the model's declared field names. Any drift (column added without a model
field, or vice versa) fails loudly.

Covers the channel-rotation tables (`ads`, `channels`); the rest of the v1
models are covered by the integration tests and don't need re-checking on
every run.
"""

from __future__ import annotations

import re
from pathlib import Path

from soshop_server.db.models import Ad, Channel

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
SQL_FILE = MIGRATIONS_DIR / "0001_initial_schema.up.sql"


def _extract_columns(sql: str, table: str) -> list[str]:
    """Return the column names declared in ``CREATE TABLE <table> (...)``."""

    match = re.search(
        rf"CREATE TABLE {table}\s*\((.*?)\);",
        sql,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"CREATE TABLE {table} not found in {SQL_FILE}"

    body = match.group(1)
    columns: list[str] = []
    depth = 0
    buf: list[str] = []
    # Split on top-level commas only — column definitions like
    # CHECK (status IN ('a', 'b')) contain commas inside parens.
    for ch in body:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            columns.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        columns.append("".join(buf).strip())

    names: list[str] = []
    for raw in columns:
        line = raw.strip()
        if not line:
            continue
        first = line.split()[0].upper()
        if first in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}:
            continue
        names.append(line.split()[0])
    return names


def test_ad_model_matches_ads_columns() -> None:
    sql = SQL_FILE.read_text()
    columns = set(_extract_columns(sql, "ads"))
    fields = set(Ad.model_fields)
    assert fields == columns, (
        f"Ad model fields drifted from ads columns: "
        f"only in model={fields - columns}, only in SQL={columns - fields}"
    )


def test_channel_model_matches_channels_columns() -> None:
    sql = SQL_FILE.read_text()
    columns = set(_extract_columns(sql, "channels"))
    fields = set(Channel.model_fields)
    assert fields == columns, (
        f"Channel model fields drifted from channels columns: "
        f"only in model={fields - columns}, only in SQL={columns - fields}"
    )


def test_ads_sequence_active_idx_exists() -> None:
    """The partial index is what the rotation reader relies on for cheap scans."""

    sql = SQL_FILE.read_text()
    pattern = re.compile(
        r"CREATE INDEX\s+ads_sequence_active_idx\s+ON\s+ads\s*\(\s*sequence_order\s*\)"
        r"\s+WHERE\s+status\s*=\s*'active'",
        re.IGNORECASE,
    )
    assert pattern.search(sql), "ads_sequence_active_idx partial index is missing"
