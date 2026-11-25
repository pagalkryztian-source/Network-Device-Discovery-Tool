from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class RateLimiter:
    """Thread-safe limiter: callers of acquire() are spaced >= 1/rate seconds apart."""

    def __init__(self, rate_per_s: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate_per_s
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self, cancel: Optional[threading.Event] = None) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self._interval
            wait = start - now
        if wait > 0:
            if cancel is not None:
                cancel.wait(wait)
            else:
                self._sleep(wait)
