"""Animated radar drawn on a Tk canvas. Purely visual: it never touches the network.

Canvas items are created once per device and only re-configured when their brightness level
changes (8 levels), so the per-frame cost stays low with hundreds of devices.
"""
from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable, Optional

from devices.device import Category, Device, Status
from ui import theme
from ui.radar_math import (RING_METRES, SECTORS, UNMEASURED_BAND, blend, blip_position, distance_frac,
                           polar_to_xy, shape_points, sweep_intensity)

FRAME_MS = 33
TRAIL_STEPS = 28
TRAIL_STEP_DEG = 2.0
LEVELS = 8
PING_S = 1.4


@dataclass
class _Blip:
    key: str
    category: Category
    online: bool
    sector: str
    distance: Optional[float]
    angle: float
    frac: float
    item: int = 0
    level: int = -1
    visible: bool = True
    x: float = 0.0
    y: float = 0.0


@dataclass
class _Ping:
    key: str
    start: float
    item: int = 0


class RadarRenderer:
    def __init__(self, canvas: tk.Canvas, sweep_period_s: float = 4.5,
                 on_click: Optional[Callable[[str], None]] = None) -> None:
        self.c = canvas
        self.period = sweep_period_s
        self.on_click = on_click
        self._blips: dict[str, _Blip] = {}
        self._pings: list[_Ping] = []
        self._filter: Optional[frozenset[Category]] = None
        self._cx = self._cy = self._r = 0.0
        self._trail: list[int] = []
        self._line = 0
        self._t0 = time.monotonic()
        self._after: Optional[str] = None
        self._running = False
        canvas.configure(bg=theme.BG, highlightthickness=0)
        canvas.bind("<Configure>", lambda _e: self._rebuild())
        canvas.bind("<Button-1>", self._clicked)

    # ------------------------------------------------------------ public API
    def start(self) -> None:
        if not self._running:
            self._running = True
            self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after:
            self.c.after_cancel(self._after)
            self._after = None

    def upsert(self, dev: Device, announce: bool = False) -> None:
        cat = dev.effective_category
        online = dev.status is Status.ONLINE
        sector = dev.rssi_source or ("bluetooth" if dev.transport == "bluetooth" else "ip")
        blip = self._blips.get(dev.key)
        if blip is None:
            angle, frac = blip_position(dev.key, sector, dev.distance_m)
            blip = self._blips[dev.key] = _Blip(dev.key, cat, online, sector, dev.distance_m, angle, frac)
            blip.visible = self._passes(cat)
            self._draw_blip(blip)
            if announce and blip.visible:
                self._start_ping(blip)
        elif (blip.category is not cat or blip.online != online or blip.sector != sector
              or (dev.distance_m is not None and abs(distance_frac(dev.distance_m) - blip.frac) > 0.004)):
            blip.angle, blip.frac = blip_position(dev.key, sector, dev.distance_m)
            blip.category, blip.online, blip.sector, blip.distance, blip.level = cat, online, sector, dev.distance_m, -1
            blip.visible = self._passes(cat)
            self.c.delete(blip.item)
            self._draw_blip(blip)

    def remove(self, key: str) -> None:
        blip = self._blips.pop(key, None)
        if blip:
            self.c.delete(blip.item)

    def clear(self) -> None:
        for b in self._blips.values():
            self.c.delete(b.item)
        self._blips.clear()
        for p in self._pings:
            self.c.delete(p.item)
        self._pings.clear()

    def set_filter(self, categories: Optional[frozenset[Category]]) -> None:
        self._filter = categories
        for b in self._blips.values():
            b.visible = self._passes(b.category)
            self.c.itemconfigure(b.item, state="normal" if b.visible else "hidden")

    # ------------------------------------------------------------ drawing
    def _passes(self, cat: Category) -> bool:
        return self._filter is None or cat in self._filter

    def _rebuild(self) -> None:
        w, h = max(self.c.winfo_width(), 50), max(self.c.winfo_height(), 50)
        self._cx, self._cy = w / 2, h / 2
        self._r = max(min(w, h) / 2 - 26, 20)
        self.c.delete("static")
        self.c.delete("sweep")
        cx, cy, r = self._cx, self._cy, self._r
        for metres in RING_METRES:
            rr = r * distance_frac(metres)
            self.c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=theme.GRID, tags="static")
            self.c.create_text(cx + 4, cy - rr - 1, text=f"{metres:g} m", anchor="sw", fill=theme.MUTED,
                               font=theme.FONT_TINY, tags="static")
        band_lo, band_hi = UNMEASURED_BAND
        mid = r * (band_lo + band_hi) / 2
        self.c.create_oval(cx - mid, cy - mid, cx + mid, cy + mid, outline=theme.GRID, dash=(3, 5), tags="static")
        for start, width, label in SECTORS.values():
            x, y = polar_to_xy(cx, cy, r, start, 1.0)
            self.c.create_line(cx, cy, x, y, fill=theme.GRID, tags="static")
            lx, ly = polar_to_xy(cx, cy, r + 11, start + width / 2, 1.0)
            self.c.create_text(lx, ly, text=label, fill=theme.MUTED, font=theme.FONT_TINY, tags="static")
        self.c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=theme.ACCENT, outline="", tags="static")
        self._trail = [
            self.c.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=TRAIL_STEP_DEG + 0.6,
                              style=tk.PIESLICE, outline="",
                              fill=blend(theme.ACCENT, theme.BG, 0.22 * (1 - i / TRAIL_STEPS)), tags="sweep")
            for i in range(TRAIL_STEPS)]
        self._line = self.c.create_line(cx, cy, cx + r, cy, fill=theme.ACCENT, width=2, tags="sweep")
        self.c.tag_lower("sweep")
        self.c.tag_lower("static")
        for b in self._blips.values():
            self.c.delete(b.item)
            b.level = -1
            self._draw_blip(b)

    def _draw_blip(self, b: _Blip) -> None:
        b.x, b.y = polar_to_xy(self._cx, self._cy, self._r, b.angle, b.frac)
        color, shape = theme.MARKERS[b.category]
        size = 5.0
        opts = dict(fill=color if b.online else "", outline=color if b.online else theme.OFFLINE,
                    width=1 if b.online else 2, tags="blip")
        if not b.online:
            opts["dash"] = (2, 2)
        pts = shape_points(shape, b.x, b.y, size)
        if pts is None:
            b.item = self.c.create_oval(b.x - size, b.y - size, b.x + size, b.y + size, **opts)
        else:
            b.item = self.c.create_polygon(*pts, **opts)
        if not b.visible:
            self.c.itemconfigure(b.item, state="hidden")

    def _start_ping(self, b: _Blip) -> None:
        item = self.c.create_oval(b.x, b.y, b.x, b.y, outline=theme.ACCENT, width=2, tags="ping")
        self._pings.append(_Ping(b.key, time.monotonic(), item))

    # ------------------------------------------------------------ animation
    def _tick(self) -> None:
        if not self._running:
            return
        now = time.monotonic()
        sweep = ((now - self._t0) / self.period * 360.0) % 360.0
        if self._trail:
            for i, item in enumerate(self._trail):
                self.c.itemconfigure(item, start=-(sweep - i * TRAIL_STEP_DEG))
            x, y = polar_to_xy(self._cx, self._cy, self._r, sweep, 1.0)
            self.c.coords(self._line, self._cx, self._cy, x, y)
        for b in self._blips.values():
            if not b.online or not b.visible:
                continue
            level = int(sweep_intensity(sweep, b.angle) * (LEVELS - 1))
            if level != b.level:
                b.level = level
                color = blend(theme.MARKERS[b.category][0], theme.BG, 0.38 + 0.62 * level / (LEVELS - 1))
                self.c.itemconfigure(b.item, fill=color)
        alive: list[_Ping] = []
        for p in self._pings:
            blip = self._blips.get(p.key)
            t = (now - p.start) / PING_S
            if t >= 1.0 or blip is None:
                self.c.delete(p.item)
                continue
            rad = 6 + 26 * t
            self.c.coords(p.item, blip.x - rad, blip.y - rad, blip.x + rad, blip.y + rad)
            self.c.itemconfigure(p.item, outline=blend(theme.ACCENT, theme.BG, 1.0 - t))
            alive.append(p)
        self._pings = alive
        self._after = self.c.after(FRAME_MS, self._tick)

    def _clicked(self, event: tk.Event) -> None:
        if not self.on_click:
            return
        best, best_d = None, 12.0 ** 2
        for b in self._blips.values():
            if not b.visible:
                continue
            d = (b.x - event.x) ** 2 + (b.y - event.y) ** 2
            if d <= best_d:
                best, best_d = b.key, d
        if best:
            self.on_click(best)
