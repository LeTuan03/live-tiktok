"""Database models package — imports all models for SQLAlchemy metadata registration."""

from app.database.models.base import Base, IDMixin, TimestampMixin
from app.database.models.product import Product, ProductVariant
from app.database.models.media import MediaFile, ProductMedia
from app.database.models.script import Script, ScriptVersion
from app.database.models.voice import VoiceProfile, GeneratedAudio
from app.database.models.playlist import Playlist, PlaylistItem
from app.database.models.live_session import LiveSession, SchedulerJob
from app.database.models.comment import CommentEvent, AISuggestion
from app.database.models.settings import IntegrationSetting, AppSetting, ActivityLog

__all__ = [
    "Base",
    "IDMixin",
    "TimestampMixin",
    "Product",
    "ProductVariant",
    "MediaFile",
    "ProductMedia",
    "Script",
    "ScriptVersion",
    "VoiceProfile",
    "GeneratedAudio",
    "Playlist",
    "PlaylistItem",
    "LiveSession",
    "SchedulerJob",
    "CommentEvent",
    "AISuggestion",
    "IntegrationSetting",
    "AppSetting",
    "ActivityLog",
]
