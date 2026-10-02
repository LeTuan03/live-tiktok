"""Secret storage backed by the OS credential store (Windows Credential Manager).

API keys, OBS passwords and stream keys must never be written to the database
or log files in plaintext. This module wraps ``keyring`` and degrades
gracefully when no backend is available (e.g. headless CI), in which case the
caller may decide to fall back to database storage.
"""

from __future__ import annotations

from loguru import logger

SERVICE_NAME = "LiveFlowAIStudio"

try:  # pragma: no cover - import guard
    import keyring
    from keyring.errors import KeyringError

    _KEYRING_AVAILABLE = True
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]
    KeyringError = Exception  # type: ignore[assignment,misc]
    _KEYRING_AVAILABLE = False


def keyring_available() -> bool:
    """Return True when a usable keyring backend is present."""
    if not _KEYRING_AVAILABLE:
        return False
    try:
        backend = keyring.get_keyring()
        # The "fail" backend raises on every operation; treat it as unavailable.
        return "fail" not in type(backend).__module__.lower()
    except Exception:
        return False


def set_secret(key: str, value: str) -> bool:
    """Store a secret. Returns True when stored in the OS credential store."""
    if not keyring_available():
        return False
    try:
        if value:
            keyring.set_password(SERVICE_NAME, key, value)
        else:
            delete_secret(key)
        return True
    except KeyringError as exc:
        logger.warning("Không thể lưu secret '{}' vào Credential Manager: {}", key, exc)
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Lỗi không xác định khi lưu secret '{}': {}", key, exc)
        return False


def get_secret(key: str) -> str | None:
    """Fetch a secret. Returns None when missing or when the store is unavailable."""
    if not keyring_available():
        return None
    try:
        return keyring.get_password(SERVICE_NAME, key)
    except KeyringError as exc:
        logger.warning("Không thể đọc secret '{}' từ Credential Manager: {}", key, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Lỗi không xác định khi đọc secret '{}': {}", key, exc)
        return None


def delete_secret(key: str) -> None:
    """Remove a secret if present (no error when missing)."""
    if not keyring_available():
        return
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except Exception:
        # PasswordDeleteError when not present — ignore
        pass


def mask_secret(value: str | None, visible: int = 4) -> str:
    """Return a masked representation safe for logs/UI (e.g. ``****abcd``)."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * 6 + value[-visible:]
