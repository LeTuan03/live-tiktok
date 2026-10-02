"""Platform integration package (TikTok LIVE, Shopee Live) — capability based."""

from app.integrations.tiktok.capabilities import (
    Capability,
    CapabilityInfo,
    CapabilityStatus,
    Platform,
    get_capabilities,
    get_capability,
    is_supported,
    rtmp_hint,
)

__all__ = [
    "Capability",
    "CapabilityInfo",
    "CapabilityStatus",
    "Platform",
    "get_capabilities",
    "get_capability",
    "is_supported",
    "rtmp_hint",
]
