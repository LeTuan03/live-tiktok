"""Verify the FTC disclosure flags on the `/feed/cursor` payload.

Both flags are required by the FTC Endorsement Guides — see
`server/legal/disclosure.md`. These tests fail if either flag goes
missing from the response schema.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from soshop.api.schemas.feed import FeedCursorResponse, FeedItemOut, PersonaOut


def _slot(*, product_id=None) -> FeedItemOut:
    return FeedItemOut(
        persona=PersonaOut(id=uuid4(), is_ai_creator=True),
        product=product_id,
        script_id=uuid4(),
        is_ad=product_id is not None,
    )


def test_persona_carries_is_ai_creator_flag() -> None:
    persona = PersonaOut(id=uuid4(), is_ai_creator=True)
    payload = persona.model_dump()
    assert "is_ai_creator" in payload
    assert payload["is_ai_creator"] is True


def test_persona_is_ai_creator_is_required() -> None:
    """The flag is mandatory: omitting it must raise a validation error."""
    with pytest.raises(ValidationError):
        PersonaOut(id=uuid4())  # type: ignore[call-arg]


def test_feed_item_carries_is_ad_flag() -> None:
    ad_slot = _slot(product_id=uuid4())
    comedy_slot = _slot(product_id=None)

    ad_payload = ad_slot.model_dump()
    comedy_payload = comedy_slot.model_dump()

    assert "is_ad" in ad_payload
    assert ad_payload["is_ad"] is True
    assert "is_ad" in comedy_payload
    assert comedy_payload["is_ad"] is False


def test_feed_item_is_ad_is_required() -> None:
    with pytest.raises(ValidationError):
        FeedItemOut(  # type: ignore[call-arg]
            persona=PersonaOut(id=uuid4(), is_ai_creator=True),
            script_id=uuid4(),
        )


def test_feed_cursor_response_round_trips_both_flags() -> None:
    """End-to-end: serializing a full response surfaces both flags on
    every slot, which is the contract the client relies on."""
    response = FeedCursorResponse(
        items=[_slot(product_id=uuid4()), _slot(product_id=None)],
        next_cursor=None,
    )

    payload = response.model_dump()

    assert len(payload["items"]) == 2
    for item in payload["items"]:
        assert "is_ad" in item
        assert "is_ai_creator" in item["persona"]
        assert item["persona"]["is_ai_creator"] is True
