"""Signal-strength maths (pure functions, no I/O).

Distance from RSSI uses the log-distance path-loss model:

    d = 10 ** ((ref_rssi - rssi) / (10 * n))

ref_rssi is the RSSI measured at 1 m, n is the path-loss exponent (2.0 free space,
2.5-4 indoors). Real RSSI fluctuates by several dB because of walls, bodies and
multipath, so the result is an ESTIMATE: this module always reports a range, never a
single "exact" value.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

MIN_DISTANCE_M = 0.3
MAX_DISTANCE_M = 200.0
SHADOWING_SIGMA_DB = 6.0        # typical indoor RSSI standard deviation


@dataclass(frozen=True)
class PathLossModel:
    ref_rssi: float             # dBm at 1 m
    exponent: float             # path-loss exponent n


def rssi_to_distance(rssi: float, model: PathLossModel) -> float:
    d = 10 ** ((model.ref_rssi - rssi) / (10.0 * model.exponent))
    return max(MIN_DISTANCE_M, min(MAX_DISTANCE_M, d))


def distance_range(distance: float, model: PathLossModel,
                   sigma_db: float = SHADOWING_SIGMA_DB) -> tuple[float, float]:
    """(low, high) distance for +/- one sigma of RSSI shadowing."""
    factor = 10 ** (sigma_db / (10.0 * model.exponent))
    return distance / factor, distance * factor


def format_distance(distance: Optional[float], model: Optional[PathLossModel]) -> str:
    if distance is None or model is None:
        return "-"
    lo, hi = distance_range(distance, model)
    if hi < 10:
        return f"~{distance:.1f} m ({lo:.1f}-{hi:.1f})"
    return f"~{distance:.0f} m ({lo:.0f}-{hi:.0f})"


def signal_quality(rssi: Optional[float]) -> str:
    if rssi is None:
        return "-"
    if rssi >= -55:
        return "excellent"
    if rssi >= -67:
        return "good"
    if rssi >= -80:
        return "fair"
    return "weak"


class KalmanRssi:
    """1-D Kalman filter for RSSI (random-walk state, noisy measurements).

    Smooths the jumpy readings without lagging like a long moving average.
    q = how fast the true signal may drift per update (dB^2)
    r = measurement noise variance (dB^2)
    """

    def __init__(self, q: float = 1.0, r: float = 16.0) -> None:
        self.q, self.r = q, r
        self.x: Optional[float] = None
        self.p = 0.0

    def update(self, measurement: float) -> float:
        if self.x is None:
            self.x, self.p = float(measurement), self.r
            return self.x
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.x += k * (measurement - self.x)
        self.p *= (1.0 - k)
        return self.x


class DistanceTrend:
    """Smoothed approaching/receding/steady estimate from successive distance readings.

    This is NOT direction/bearing (no antenna array can give that here) - it only answers
    "is the smoothed distance estimate getting smaller or larger over time", which is enough
    to support a "getting warmer / colder" search workflow. The deadband absorbs normal RSSI
    jitter so the label does not flicker every reading.
    """

    def __init__(self, alpha: float = 0.35, deadband_m: float = 0.3, min_samples: int = 3) -> None:
        self._alpha = alpha
        self._deadband = deadband_m
        self._min_samples = min_samples
        self._slope: Optional[float] = None
        self._last: Optional[float] = None
        self._n = 0

    def update(self, distance_m: float) -> Optional[str]:
        """Feed one new (already Kalman-smoothed) distance reading; returns the current label."""
        self._n += 1
        if self._last is not None:
            delta = distance_m - self._last
            self._slope = delta if self._slope is None else (self._alpha * delta + (1 - self._alpha) * self._slope)
        self._last = distance_m
        return self.label

    @property
    def label(self) -> Optional[str]:
        if self._n < self._min_samples or self._slope is None:
            return None
        if self._slope <= -self._deadband:
            return "approaching"
        if self._slope >= self._deadband:
            return "receding"
        return "steady"

    @property
    def slope_m_per_reading(self) -> Optional[float]:
        return self._slope


def wifi_channel(freq_mhz: Optional[float]) -> Optional[int]:
    if not freq_mhz:
        return None
    f = int(round(freq_mhz))
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if 5000 <= f < 5950:
        return (f - 5000) // 5
    if 5950 <= f <= 7125:
        return (f - 5950) // 5
    return None


def wifi_band(freq_mhz: Optional[float]) -> Optional[str]:
    if not freq_mhz:
        return None
    return "2.4 GHz" if freq_mhz < 3000 else ("5 GHz" if freq_mhz < 5950 else "6 GHz")


_ESC = re.compile(r"\\x([0-9a-fA-F]{2})")


def decode_ssid(raw: str) -> Optional[str]:
    """Decode `iw` SSID escapes (\\xNN). Hidden networks (empty / all NUL) return None."""
    data = bytearray()
    pos = 0
    for m in _ESC.finditer(raw):
        data += raw[pos:m.start()].encode("utf-8", "replace")
        data.append(int(m.group(1), 16))
        pos = m.end()
    data += raw[pos:].encode("utf-8", "replace")
    text = data.decode("utf-8", "replace").replace("\x00", "").strip()
    return text or None


def median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty list")
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def log_scale_fraction(distance: float, lo: float, hi: float) -> float:
    """0..1 position of `distance` on a logarithmic axis lo..hi."""
    distance = max(lo, min(hi, distance))
    return math.log(distance / lo) / math.log(hi / lo)
