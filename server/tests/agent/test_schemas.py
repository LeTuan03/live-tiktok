"""Unit tests for channel_id / ad_start_index on DispatchMetadata."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from soshop.agent.schemas import DispatchMetadata


def _base_payload() -> dict:
    return {
        "persona_id": "persona_alex",
        "product_id": "prod_123",
        "script_id": "script_v1",
        "pitch_window_ms": 30_000,
        "popup_payload": None,
        "session_id": "sess_abc",
        "user_id": "user_xyz",
    }


def test_one_to_one_dispatch_omits_channel_fields() -> None:
    md = DispatchMetadata.model_validate(_base_payload())
    assert md.channel_id is None
    assert md.ad_start_index is None


def test_channel_dispatch_requires_ad_start_index() -> None:
    payload = _base_payload() | {"channel_id": "chan_42", "ad_start_index": 3}
    md = DispatchMetadata.model_validate(payload)
    assert md.channel_id == "chan_42"
    assert md.ad_start_index == 3


def test_channel_id_without_ad_start_index_rejected() -> None:
    payload = _base_payload() | {"channel_id": "chan_42"}
    with pytest.raises(ValidationError):
        DispatchMetadata.model_validate(payload)
