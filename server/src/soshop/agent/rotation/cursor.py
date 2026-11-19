"""Pure-state rotation cursor for the channel broadcast loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")


@dataclass
class RotationCursor:
    """Modulo-wrapping cursor over a fixed-length rotation.

    Extracted from the previous ``cursor % len(ads)`` arithmetic so the
    orchestrator can advance via a named call. Keeping this trivial and
    state-only lets the orchestrator stay focused on async I/O.
    """

    index: int = 0

    def advance(self) -> None:
        self.index += 1

    def current(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("RotationCursor.current: items is empty")
        return items[self.index % len(items)]
