"""Presenter module — AI presenter identity, gesture clip library, and avatar providers.

Factory function `create_provider()` returns the correct PresenterProvider
based on configuration, making it easy for the render pipeline to get started.
"""

from app.presenter.base import PresenterProvider, PresenterClip
from app.presenter.clip_library import PresenterClipLibrary
from app.presenter.cloud_i2v import CloudI2VProvider
from app.presenter.cloud_lipsync import CloudLipsyncProvider


def create_provider(
    service_name: str = "did",
    api_key: str = "",
    avatar_id: str = "",
) -> PresenterProvider:
    """Factory: create the right PresenterProvider based on service_name.

    Args:
        service_name: 'did', 'heygen', 'lipsync', or 'generic'
        api_key: API key override (otherwise read from settings/.env)
        avatar_id: Platform-specific avatar ID (HeyGen avatar_id, D-ID presenter_id)

    Returns:
        A configured PresenterProvider ready to use.
    """
    if service_name in ("did", "heygen", "generic"):
        return CloudI2VProvider(
            api_key=api_key or None,
            service_name=service_name,
            avatar_id=avatar_id or None,
        )
    elif service_name == "lipsync":
        return CloudLipsyncProvider(api_key=api_key or None)
    else:
        # Default to lipsync provider (works offline with ffmpeg)
        return CloudLipsyncProvider(api_key=api_key or None)


__all__ = [
    "PresenterProvider",
    "PresenterClip",
    "PresenterClipLibrary",
    "CloudI2VProvider",
    "CloudLipsyncProvider",
    "create_provider",
]
