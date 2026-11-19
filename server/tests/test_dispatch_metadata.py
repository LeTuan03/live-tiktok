"""Unit tests for the agent dispatch metadata schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from soshop.agent.schemas import (
    DispatchMetadata,
    DispatchMetadataValidationError,
    PopupPayload,
    parse_dispatch_metadata,
)
from soshop.livekit.dispatch import build_dispatch_request


def _valid_payload() -> dict:
    return {
        "persona_id": "persona_alex",
        "product_id": "prod_123",
        "script_id": "script_v1",
        "pitch_window_ms": 30_000,
        "popup_payload": {
            "id": "popup_1",
            "title": "Get 20% off",
            "image_url": "https://cdn.plug.test/p/1.jpg",
            "cta": "Shop now",
            "deep_link_url": "plug://product/prod_123",
            "landing_url": "https://plug.test/p/prod_123",
        },
        "session_id": "sess_abc",
        "user_id": "user_xyz",
    }


def test_valid_payload_parses() -> None:
    md = DispatchMetadata.model_validate(_valid_payload())
    assert md.persona_id == "persona_alex"
    assert md.product_id == "prod_123"
    assert md.pitch_window_ms == 30_000
    assert md.popup_payload is not None
    assert md.popup_payload.cta == "Shop now"


def test_optional_fields_can_be_null() -> None:
    payload = _valid_payload()
    payload["product_id"] = None
    payload["popup_payload"] = None
    md = DispatchMetadata.model_validate(payload)
    assert md.product_id is None
    assert md.popup_payload is None


def test_round_trip_via_metadata_string() -> None:
    md = DispatchMetadata.model_validate(_valid_payload())
    raw = md.to_metadata_string()
    decoded = parse_dispatch_metadata(raw)
    assert decoded == md


def test_parse_rejects_empty_string() -> None:
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata("")


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata("{not json")


def test_missing_required_field_rejected() -> None:
    payload = _valid_payload()
    del payload["persona_id"]
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata(json.dumps(payload))


def test_unknown_field_rejected() -> None:
    payload = _valid_payload()
    payload["surprise"] = "boom"
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata(json.dumps(payload))


def test_negative_pitch_window_rejected() -> None:
    payload = _valid_payload()
    payload["pitch_window_ms"] = -1
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata(json.dumps(payload))


def test_empty_persona_id_rejected() -> None:
    payload = _valid_payload()
    payload["persona_id"] = ""
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata(json.dumps(payload))


def test_popup_payload_missing_field_rejected() -> None:
    payload = _valid_payload()
    del payload["popup_payload"]["cta"]
    with pytest.raises(DispatchMetadataValidationError):
        parse_dispatch_metadata(json.dumps(payload))


def test_popup_payload_validates_independently() -> None:
    with pytest.raises(ValidationError):
        PopupPayload.model_validate(
            {
                "id": "",
                "title": "x",
                "image_url": "x",
                "cta": "x",
                "deep_link_url": "x",
                "landing_url": "x",
            }
        )


def test_build_dispatch_request_validates_metadata_type() -> None:
    with pytest.raises(TypeError):
        build_dispatch_request(
            agent_name="plug-agent",
            room="room_1",
            metadata={"persona_id": "x"},  # type: ignore[arg-type]
        )


def test_build_dispatch_request_requires_agent_name() -> None:
    md = DispatchMetadata.model_validate(_valid_payload())
    with pytest.raises(ValueError):
        build_dispatch_request(agent_name="", room="room_1", metadata=md)


def test_build_dispatch_request_requires_room() -> None:
    md = DispatchMetadata.model_validate(_valid_payload())
    with pytest.raises(ValueError):
        build_dispatch_request(agent_name="plug-agent", room="", metadata=md)


def test_build_dispatch_request_serializes_metadata() -> None:
    md = DispatchMetadata.model_validate(_valid_payload())
    req = build_dispatch_request(agent_name="plug-agent", room="room_1", metadata=md)
    assert req.agent_name == "plug-agent"
    assert req.room == "room_1"
    decoded = json.loads(req.metadata)
    assert decoded["persona_id"] == "persona_alex"
    assert decoded["session_id"] == "sess_abc"
