"""Presenter module — AI presenter identity, gesture clip library, and avatar providers."""

from app.presenter.base import PresenterProvider, PresenterClip
from app.presenter.clip_library import PresenterClipLibrary

__all__ = ["PresenterProvider", "PresenterClip", "PresenterClipLibrary"]
