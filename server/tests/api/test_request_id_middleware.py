"""Tests for the X-Request-ID middleware + log filter.

Hermetic: uses FastAPI ``TestClient`` against a freshly-constructed app
that only mounts a single probe route plus our middleware/filter — no DB,
no LiveKit, no S3.
"""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop_server.api.middleware import (
    REQUEST_ID_HEADER,
    RequestIDLogFilter,
    RequestIDMiddleware,
    request_id_var,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        # Emit a log line inside the request so the caplog assertion below
        # can verify the filter populates record.request_id.
        logging.getLogger("test.probe").info("probe handler ran")
        return {"request_id": request_id_var.get()}

    return app


def test_missing_header_generates_uuid_hex_request_id() -> None:
    client = TestClient(_build_app())
    response = client.get("/probe")
    assert response.status_code == 200
    rid = response.headers[REQUEST_ID_HEADER]
    assert _HEX32.match(rid), f"expected uuid4 hex, got {rid!r}"
    # The handler echoed the contextvar value back — should equal the
    # response header so we know the same ID flowed end-to-end.
    assert response.json() == {"request_id": rid}


def test_incoming_request_id_is_echoed_unchanged() -> None:
    client = TestClient(_build_app())
    incoming = "incoming-trace-id-1234"
    response = client.get("/probe", headers={REQUEST_ID_HEADER: incoming})
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == incoming
    assert response.json() == {"request_id": incoming}


def test_log_records_carry_request_id(caplog: pytest.LogCaptureFixture) -> None:
    # Install the filter on caplog's handler so record.request_id is
    # populated by the time caplog snapshots it. The filter runs against
    # the contextvar that's only set while the request is in flight.
    caplog.handler.addFilter(RequestIDLogFilter())
    client = TestClient(_build_app())

    incoming = uuid.uuid4().hex
    with caplog.at_level(logging.INFO, logger="test.probe"):
        response = client.get("/probe", headers={REQUEST_ID_HEADER: incoming})

    assert response.status_code == 200
    matching = [
        rec
        for rec in caplog.records
        if rec.name == "test.probe" and rec.getMessage() == "probe handler ran"
    ]
    assert matching, "expected the probe handler's log record to be captured"
    assert getattr(matching[0], "request_id", None) == incoming


def test_contextvar_resets_after_request() -> None:
    # Sanity check: once a request completes, the contextvar in the test
    # task should be back to its default empty string. (The TestClient
    # runs each request in its own event loop / task, so this is mostly
    # a guard against accidental leakage via globals.)
    client = TestClient(_build_app())
    client.get("/probe")
    assert request_id_var.get() == ""
