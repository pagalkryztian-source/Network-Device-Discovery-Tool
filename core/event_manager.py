"""Thread-safe event bus. Worker threads publish(); the GUI thread drains/dispatches."""
from __future__ import annotations

import logging
import queue
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from config.defaults import EVENT_QUEUE_MAX

log = logging.getLogger(__name__)


class EventType(Enum):
    SCAN_STARTED = auto()
    SCAN_STOPPED = auto()
    CYCLE_STARTED = auto()
    CYCLE_COMPLETED = auto()
    DEVICE_DISCOVERED = auto()
    DEVICE_UPDATED = auto()
    DEVICE_OFFLINE = auto()
    DEVICE_REAPPEARED = auto()
    CLASSIFICATION_UPDATED = auto()
    SCAN_ERROR = auto()
    TARGET_ALERT = auto()          # a tracked (search-mode) device: very close / lost / reappeared


@dataclass(frozen=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Bounded queue. If the consumer stalls, the oldest event is dropped (and logged)."""

    def __init__(self, maxsize: int = EVENT_QUEUE_MAX) -> None:
        self._q: "queue.Queue[Event]" = queue.Queue(maxsize=maxsize)
        self._subs: dict[EventType, list[Callable[[Event], None]]] = defaultdict(list)
        self._dropped = 0

    def publish(self, type_: EventType, **payload: Any) -> None:
        ev = Event(type_, payload)
        while True:
            try:
                self._q.put_nowait(ev)
                return
            except queue.Full:
                try:
                    self._q.get_nowait()
                    self._dropped += 1
                    if self._dropped in (1, 100, 1000) or self._dropped % 10000 == 0:
                        log.warning("Event queue full; dropped %d event(s)", self._dropped)
                except queue.Empty:
                    pass

    def subscribe(self, type_: EventType, callback: Callable[[Event], None]) -> None:
        self._subs[type_].append(callback)

    def drain(self, max_items: int = 500) -> list[Event]:
        out: list[Event] = []
        while len(out) < max_items:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def dispatch(self, max_items: int = 500) -> int:
        """Drain and call subscribers (call from the GUI thread only)."""
        events = self.drain(max_items)
        for ev in events:
            for cb in self._subs.get(ev.type, ()):
                try:
                    cb(ev)
                except Exception:
                    log.exception("Event subscriber failed for %s", ev.type.name)
        return len(events)
