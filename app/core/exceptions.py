"""Custom exception hierarchy for LiveFlow AI Studio."""

from __future__ import annotations


class LiveFlowError(Exception):
    """Base exception for all LiveFlow errors."""

    def __init__(self, message: str = "", detail: str = "") -> None:
        self.detail = detail
        super().__init__(message)


# --- Configuration ---
class ConfigError(LiveFlowError):
    """Configuration-related errors."""


class MissingDependencyError(ConfigError):
    """A required external dependency is missing."""

    def __init__(self, dependency: str, install_hint: str = "") -> None:
        self.dependency = dependency
        self.install_hint = install_hint
        msg = f"Thiếu dependency: {dependency}"
        if install_hint:
            msg += f" — {install_hint}"
        super().__init__(msg)


# --- Database ---
class DatabaseError(LiveFlowError):
    """Database operation errors."""


class RecordNotFoundError(DatabaseError):
    """Requested record does not exist."""

    def __init__(self, model: str, record_id: int | str) -> None:
        super().__init__(f"{model} với ID={record_id} không tồn tại")


# --- Media ---
class MediaError(LiveFlowError):
    """Media file operation errors."""


class MediaFileNotFoundError(MediaError):
    """Referenced media file does not exist on disk."""


class MediaFormatError(MediaError):
    """Unsupported or corrupt media format."""


class MediaFileTooLargeError(MediaError):
    """Media file exceeds the maximum allowed size."""

    def __init__(self, filename: str, size_mb: float, max_mb: int) -> None:
        super().__init__(
            f"File '{filename}' ({size_mb:.1f} MB) vượt giới hạn {max_mb} MB"
        )


# --- AI / LLM ---
class AIError(LiveFlowError):
    """AI provider errors."""


class AIProviderNotConfiguredError(AIError):
    """AI provider is not configured (missing API key, etc.)."""


class AIRequestError(AIError):
    """AI API request failed."""


class AIContentPolicyError(AIError):
    """AI generated content that violates policies (unverified claims, etc.)."""


class AIAvatarError(AIError):
    """AI Avatar generation or lip-sync error."""


class AudioRenderError(LiveFlowError):
    """Audio or video rendering/compositing error."""


# --- TTS ---
class TTSError(LiveFlowError):
    """Text-to-Speech errors."""


class TTSProviderError(TTSError):
    """TTS provider returned an error."""


# --- OBS ---
class OBSError(LiveFlowError):
    """OBS integration errors."""


class OBSConnectionError(OBSError):
    """Cannot connect to OBS WebSocket."""


class OBSCommandError(OBSError):
    """OBS command execution failed."""


# --- TikTok ---
class TikTokError(LiveFlowError):
    """TikTok integration errors."""


class TikTokCapabilityUnsupportedError(TikTokError):
    """Requested TikTok capability is not available."""

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"Tính năng TikTok '{capability}' chưa được hỗ trợ hoặc thiếu quyền truy cập"
        )


# --- Scheduler ---
class SchedulerError(LiveFlowError):
    """Scheduler operation errors."""


# --- Import/Export ---
class ImportError_(LiveFlowError):
    """Data import errors (named with underscore to avoid shadowing builtins)."""


class ExportError(LiveFlowError):
    """Data export errors."""
