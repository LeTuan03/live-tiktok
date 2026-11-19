"""Liveness probe endpoint used by Fly.io and uptime checks."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
