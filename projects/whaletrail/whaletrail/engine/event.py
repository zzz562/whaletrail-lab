"""Event system for the WhaleTrail backtesting engine.

Defines the event types used in the event-driven architecture and an
event queue that maintains chronological ordering.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(Enum):
    """Types of events in the backtesting engine."""

    MARKET_DATA = "market_data"
    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"


@dataclass(order=True)
class Event:
    """An event in the event-driven backtesting engine.

    Events are ordered by timestamp (oldest first) so they can be
    inserted into and popped from a priority queue.

    Attributes:
        timestamp: When the event occurred.
        type: The event type (MARKET_DATA, SIGNAL, ORDER, FILL).
        data: Payload carried by the event (e.g. a bar dict, an Order, a Fill).
    """

    timestamp: datetime = field(compare=True)
    type: EventType = field(compare=False)
    data: Any = field(compare=False)


class EventQueue:
    """A time-ordered event queue (oldest event first).

    Wraps a list kept sorted by event timestamp using bisect.
    Supports push, pop, bool, and len.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []

    def push(self, event: Event) -> None:
        """Insert an event, maintaining timestamp order (oldest first)."""
        bisect.insort_right(self._events, event)

    def pop(self) -> Event:
        """Remove and return the oldest event.

        Raises:
            IndexError: If the queue is empty.
        """
        if not self._events:
            raise IndexError("pop from empty EventQueue")
        return self._events.pop(0)

    def peek(self) -> Event | None:
        """Return the oldest event without removing it, or None."""
        return self._events[0] if self._events else None

    def clear(self) -> None:
        """Remove all events from the queue."""
        self._events.clear()

    def __bool__(self) -> bool:
        return bool(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __repr__(self) -> str:
        return f"EventQueue(len={len(self._events)})"
