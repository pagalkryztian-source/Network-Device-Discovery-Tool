"""Pure geometry/colour helpers for the radar (no Tk import, so they are unit-testable)."""
from __future__ import annotations

import hashlib
import math
from typing import Optional

from utils.signal import log_scale_fraction

# Radar layout. Rings are real distances; the outer band holds devices whose distance cannot be measured.
RANGE_MIN_M = 0.5
RANGE_MAX_M = 50.0
INNER_FRAC, OUTER_FRAC = 0.10, 0.80          # radius fraction for RANGE_MIN_M .. RANGE_MAX_M
UNMEASURED_BAND = (0.88, 0.96)
RING_METRES = (1.0, 3.0, 10.0, 30.0)
# Angular sectors (degrees clockwise from east): where the evidence came from, NOT a compass bearing.
SECTORS = {"ip": (0.0, 120.0, "NETWORK"), "wifi": (120.0, 120.0, "WI-FI"), "bluetooth": (240.0, 120.0, "BLUETOOTH")}


# OBSOLETE: superseded by blip_position(); no longer used by the app, kept only for tests/test_radar_math.py.
def blip_polar(key: str) -> tuple[float, float]:
    """Stable (angle_deg clockwise from east, radius fraction 0.18..0.92) for a device key.

    The position is a deterministic layout derived from the device identity. It does NOT
    represent physical distance or direction: ARP/IP discovery cannot measure either.
    """
    h = hashlib.sha1(key.encode("utf-8")).digest()
    angle = int.from_bytes(h[:4], "big") / 2**32 * 360.0
    radius = 0.18 + int.from_bytes(h[4:8], "big") / 2**32 * 0.74
    return angle, radius


def distance_frac(distance_m: float) -> float:
    """Radius fraction for a measured distance (logarithmic: detail where it matters, up close)."""
    return INNER_FRAC + (OUTER_FRAC - INNER_FRAC) * log_scale_fraction(distance_m, RANGE_MIN_M, RANGE_MAX_M)


def blip_position(key: str, sector: str, distance_m: Optional[float]) -> tuple[float, float]:
    """(angle_deg, radius_fraction) for a device.

    Radius is a real estimate when distance_m is known, else a slot in the 'distance unknown' band.
    The angle is NOT a direction: a single receiver cannot measure bearing. It only places the
    device inside the sector of the technology that detected it (stable per device).
    """
    start, width, _label = SECTORS.get(sector, SECTORS["ip"])
    h = hashlib.sha1(key.encode("utf-8")).digest()
    angle = start + (0.12 + 0.76 * int.from_bytes(h[:4], "big") / 2**32) * width
    if distance_m is not None:
        return angle, distance_frac(distance_m)
    lo, hi = UNMEASURED_BAND
    return angle, lo + (hi - lo) * int.from_bytes(h[4:8], "big") / 2**32


def polar_to_xy(cx: float, cy: float, radius_px: float, angle_deg: float, frac: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return cx + math.cos(a) * radius_px * frac, cy + math.sin(a) * radius_px * frac


def sweep_intensity(sweep_deg: float, blip_deg: float) -> float:
    """1.0 right after the sweep passes the blip, decaying linearly to 0 over one revolution."""
    return 1.0 - ((sweep_deg - blip_deg) % 360.0) / 360.0


def blend(fg: str, bg: str, t: float) -> str:
    """Linear blend of two #rrggbb colours; t=1 -> fg, t=0 -> bg."""
    t = max(0.0, min(1.0, t))
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (f[i] - b[i]) * t):02x}" for i in range(3))


def shape_points(shape: str, x: float, y: float, r: float) -> list[float] | None:
    """Polygon coordinates for a marker shape; None means 'draw an oval'."""
    if shape == "circle":
        return None
    if shape == "square":
        pts = [(-r, -r), (r, -r), (r, r), (-r, r)]
    elif shape == "triangle":
        pts = [(0, -r * 1.15), (r * 1.05, r * 0.85), (-r * 1.05, r * 0.85)]
    elif shape == "diamond":
        pts = [(0, -r * 1.25), (r * 1.25, 0), (0, r * 1.25), (-r * 1.25, 0)]
    elif shape == "hexagon":
        pts = [(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i))) for i in range(6)]
    elif shape == "wide":
        pts = [(-r * 1.4, -r * 0.8), (r * 1.4, -r * 0.8), (r * 1.4, r * 0.8), (-r * 1.4, r * 0.8)]
    elif shape == "tall":
        pts = [(-r * 0.75, -r * 1.3), (r * 0.75, -r * 1.3), (r * 0.75, r * 1.3), (-r * 0.75, r * 1.3)]
    elif shape == "pentagon":
        pts = [(r * math.cos(math.radians(-90 + 72 * i)), r * math.sin(math.radians(-90 + 72 * i))) for i in range(5)]
    else:
        raise ValueError(f"unknown shape {shape!r}")
    return [c for px, py in pts for c in (x + px, y + py)]
