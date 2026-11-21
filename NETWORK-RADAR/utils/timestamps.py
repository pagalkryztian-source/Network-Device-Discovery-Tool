from __future__ import annotations

import time
from typing import Optional


def now() -> float:
    """Wall-clock epoch seconds (device timestamps are shown in local time)."""
    return time.time()


def fmt(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_time(ts: float) -> str:
    """Compact HH:MM:SS for table cells."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "-"
