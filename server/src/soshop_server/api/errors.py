"""Canonical error envelope for the SoShop API.

Every non-2xx response from the API uses the same JSON shape::

    {
      "error": {
        "code":    "<machine-readable_snake_case_code>",
        "message": "<human-readable string>",
        "fields":  [ { "loc": "field.path", "msg": "..." }, ... ]   # optional, 422 only
      }
    }

The shape is intentionally a single top-level `error` object so clients can
disambiguate it from a 2xx body and key off `error.code` for branching. It
replaces FastAPI's default `{"detail": ...}` envelope, which has three
distinct shapes (string, list, object) depending on the failure mode and
forced every client to re-derive the parsing rules.

Two exception handlers wire the envelope into the app:

* `validation_exception_handler` — converts FastAPI's
  `RequestValidationError` (Pydantic 422) into
  `{ "error": { "code": "validation_failed", "fields": [...] } }`.
* `http_exception_handler` — wraps `HTTPException` so any route that raises
  with `detail="message"` or `detail={"code": "...", ...}` lands in the
  envelope without per-route boilerplate.

Routes that need a structured 503 (e.g. `/feed/session` rate limit) can
raise `HTTPException(status_code=503, detail={"code": "concurrent_rooms_exhausted", ...})`
and the handler will lift the dict into `error` verbatim.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Default code used when a route raises HTTPException without supplying one.
_DEFAULT_CODES_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _default_code(status_code: int) -> str:
    return _DEFAULT_CODES_BY_STATUS.get(status_code, f"http_{status_code}")


def build_error_body(
    *,
    status_code: int,
    code: str | None = None,
    message: str,
    fields: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the canonical envelope.

    `extra` is merged into the `error` object alongside the standard
    fields — used for things like `retry_after` on 503s.
    """
    error: dict[str, Any] = {
        "code": code or _default_code(status_code),
        "message": message,
    }
    if fields:
        error["fields"] = fields
    if extra:
        for k, v in extra.items():
            if k in error:
                continue
            error[k] = v
    return {"error": error}


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Wrap any `HTTPException` in the canonical envelope.

    Detail handling:
    * `detail` is a string → message; code defaults from status.
    * `detail` is a dict   → may carry `code`, `message`, plus extra
      structured fields (e.g. `retry_after`) which are preserved.
    * `detail` is anything else → string-coerced into `message`.
    """
    headers = exc.headers or {}
    detail: Any = exc.detail

    if isinstance(detail, dict):
        code = str(detail.get("code")) if detail.get("code") is not None else None
        message = str(detail.get("message") or detail.get("code") or "")
        extra = {k: v for k, v in detail.items() if k not in {"code", "message"}}
        body = build_error_body(
            status_code=exc.status_code,
            code=code,
            message=message or _default_code(exc.status_code),
            extra=extra,
        )
    elif isinstance(detail, str):
        body = build_error_body(
            status_code=exc.status_code,
            message=detail,
        )
    else:
        body = build_error_body(
            status_code=exc.status_code,
            message=str(detail) if detail is not None else _default_code(exc.status_code),
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=dict(headers),
    )


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Convert Pydantic 422s into `{ error: { code, message, fields } }`.

    Each field error becomes `{ "loc": "field.path", "msg": "..." }`. The
    leading element of `loc` (typically "body"/"query"/"path") is
    stripped to keep the path client-friendly.
    """
    fields: list[dict[str, Any]] = []
    for err in exc.errors():
        raw_loc = err.get("loc") or ()
        # Drop the leading body/query/path marker so clients see the
        # field path the way they declared it on the wire.
        trimmed = list(raw_loc[1:]) if len(raw_loc) > 1 else list(raw_loc)
        loc = ".".join(str(part) for part in trimmed) or "_"
        fields.append({"loc": loc, "msg": err.get("msg", "Invalid value")})

    body = build_error_body(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_failed",
        message="Request failed validation.",
        fields=fields,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(body),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the canonical exception handlers on `app`.

    Idempotent: safe to call repeatedly during app construction.

    Starlette's ``add_exception_handler`` typing widens the handler
    signature to ``(Request, Exception)``, but FastAPI's runtime supports
    handlers typed against the concrete subclass and dispatches by the
    registered exception type. Cast to satisfy the type checker without
    losing the precise signatures in the handlers themselves.
    """
    from typing import cast

    from starlette.requests import Request as StarletteRequest

    HandlerT = Callable[[StarletteRequest, Exception], Awaitable[JSONResponse]]
    app.add_exception_handler(HTTPException, cast(HandlerT, http_exception_handler))
    app.add_exception_handler(RequestValidationError, cast(HandlerT, validation_exception_handler))
