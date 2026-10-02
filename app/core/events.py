"""Application-wide event bus for decoupled communication between components."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum, auto
from typing import Any, Callable

from loguru import logger


class AppEvent(Enum):
    """Application events for inter-component communication."""

    # App lifecycle
    APP_STARTED = auto()
    APP_SHUTTING_DOWN = auto()

    # Product events
    PRODUCT_CREATED = auto()
    PRODUCT_UPDATED = auto()
    PRODUCT_DELETED = auto()
    PRODUCTS_IMPORTED = auto()

    # Media events
    MEDIA_IMPORTED = auto()
    MEDIA_DELETED = auto()
    MEDIA_LINKED = auto()

    # Script events
    SCRIPT_GENERATED = auto()
    SCRIPT_UPDATED = auto()

    # TTS events
    TTS_STARTED = auto()
    TTS_COMPLETED = auto()
    TTS_FAILED = auto()
    TTS_QUEUE_CHANGED = auto()

    # OBS events
    OBS_CONNECTED = auto()
    OBS_DISCONNECTED = auto()
    OBS_SCENE_CHANGED = auto()
    OBS_STREAM_STARTED = auto()
    OBS_STREAM_STOPPED = auto()
    OBS_ERROR = auto()

    # Live session events
    SESSION_STARTED = auto()
    SESSION_PAUSED = auto()
    SESSION_RESUMED = auto()
    SESSION_STOPPED = auto()
    SESSION_PRODUCT_CHANGED = auto()

    # Scheduler events
    SCHEDULER_STARTED = auto()
    SCHEDULER_PAUSED = auto()
    SCHEDULER_STOPPED = auto()
    SCHEDULER_ITEM_STARTED = auto()
    SCHEDULER_ITEM_COMPLETED = auto()
    SCHEDULER_ERROR = auto()

    # Comment events
    COMMENT_RECEIVED = auto()
    COMMENT_REPLY_SUGGESTED = auto()
    COMMENT_REPLY_SENT = auto()
    COMMENT_REPLY_FAILED = auto()

    # Settings events
    SETTINGS_CHANGED = auto()

    # General
    STATUS_MESSAGE = auto()
    ERROR_OCCURRED = auto()


class EventBus:
    """Simple publish/subscribe event bus for decoupled component communication.

    Supports both sync and async handlers. Handlers are called in registration order.
    Exceptions in handlers are logged but don't prevent other handlers from executing.
    """

    def __init__(self) -> None:
        self._handlers: dict[AppEvent, list[Callable]] = defaultdict(list)
        self._async_handlers: dict[AppEvent, list[Callable]] = defaultdict(list)

    def subscribe(self, event: AppEvent, handler: Callable) -> None:
        """Register a synchronous handler for an event.

        Args:
            event: The event type to subscribe to.
            handler: Callable that accepts (**kwargs) with event data.
        """
        self._handlers[event].append(handler)
        logger.debug("Subscribed sync handler {} to {}", handler.__qualname__, event.name)

    def subscribe_async(self, event: AppEvent, handler: Callable) -> None:
        """Register an async handler for an event.

        Args:
            event: The event type to subscribe to.
            handler: Async callable that accepts (**kwargs) with event data.
        """
        self._async_handlers[event].append(handler)
        logger.debug("Subscribed async handler {} to {}", handler.__qualname__, event.name)

    def unsubscribe(self, event: AppEvent, handler: Callable) -> None:
        """Remove a handler from an event.

        Args:
            event: The event type to unsubscribe from.
            handler: The handler to remove.
        """
        if handler in self._handlers[event]:
            self._handlers[event].remove(handler)
        if handler in self._async_handlers[event]:
            self._async_handlers[event].remove(handler)

    def emit(self, event: AppEvent, **kwargs: Any) -> None:
        """Emit an event, calling all registered synchronous handlers.

        Args:
            event: The event to emit.
            **kwargs: Event data passed to handlers.
        """
        logger.debug("Event emitted: {} | data keys: {}", event.name, list(kwargs.keys()))
        for handler in self._handlers[event]:
            try:
                handler(**kwargs)
            except Exception:
                logger.exception(
                    "Error in sync handler {} for event {}",
                    handler.__qualname__,
                    event.name,
                )

    async def emit_async(self, event: AppEvent, **kwargs: Any) -> None:
        """Emit an event, calling all registered async handlers.

        Args:
            event: The event to emit.
            **kwargs: Event data passed to handlers.
        """
        logger.debug("Async event emitted: {} | data keys: {}", event.name, list(kwargs.keys()))
        for handler in self._async_handlers[event]:
            try:
                await handler(**kwargs)
            except Exception:
                logger.exception(
                    "Error in async handler {} for event {}",
                    handler.__qualname__,
                    event.name,
                )

    def clear(self) -> None:
        """Remove all handlers (useful for testing)."""
        self._handlers.clear()
        self._async_handlers.clear()


# Global event bus singleton
event_bus = EventBus()
