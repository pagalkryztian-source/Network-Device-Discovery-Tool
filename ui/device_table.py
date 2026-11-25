from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable, Optional

from devices.device import Device, Status
from ui import theme
from utils.signal import format_distance
from utils.timestamps import fmt_time

COLUMNS = (("name", "Device", 160), ("category", "Type", 115), ("signal", "Signal", 70), ("distance", "Distance", 115),
           ("ip", "IP address", 95), ("mac", "MAC address", 125), ("vendor", "Manufacturer", 105),
           ("status", "Status", 60), ("last", "Last seen", 75))

_SORT = {
    "name": lambda d: d.display_name.lower(), "ip": lambda d: d.ip_sort_key, "mac": lambda d: d.mac or "",
    "vendor": lambda d: (d.vendor or "").lower(), "category": lambda d: d.effective_category.value,
    "status": lambda d: d.status.value, "last": lambda d: d.last_seen,
    "signal": lambda d: -(d.rssi if d.rssi is not None else -999.0),
    "distance": lambda d: d.distance_m if d.distance_m is not None else 1e9,
}


def style_ttk(root: tk.Misc) -> None:
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure("Treeview", background=theme.PANEL, fieldbackground=theme.PANEL, foreground=theme.TEXT,
                bordercolor=theme.PANEL, lightcolor=theme.PANEL, darkcolor=theme.PANEL, rowheight=30,
                font=theme.FONT_SMALL, borderwidth=0)
    s.configure("Treeview.Heading", background=theme.PANEL, foreground=theme.MUTED, relief="flat",
                font=theme.FONT_SMALL, borderwidth=0, padding=(6, 8))
    s.map("Treeview", background=[("selected", theme.ACCENT_SOFT)], foreground=[("selected", theme.TEXT)])
    s.map("Treeview.Heading", background=[("active", theme.PANEL_ALT)])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("TCombobox", fieldbackground=theme.PANEL_ALT, background=theme.PANEL_ALT, foreground=theme.TEXT,
                arrowcolor=theme.MUTED, bordercolor=theme.BORDER, lightcolor=theme.PANEL_ALT, darkcolor=theme.PANEL_ALT)
    s.map("TCombobox", fieldbackground=[("readonly", theme.PANEL_ALT), ("disabled", theme.PANEL_ALT)],
          foreground=[("disabled", theme.OFFLINE)], selectbackground=[("readonly", theme.PANEL_ALT)],
          selectforeground=[("readonly", theme.TEXT)])
    s.configure("TSpinbox", fieldbackground=theme.PANEL_ALT, foreground=theme.TEXT, arrowcolor=theme.MUTED,
                bordercolor=theme.BORDER, lightcolor=theme.PANEL_ALT, darkcolor=theme.PANEL_ALT)
    s.configure("Vertical.TScrollbar", background=theme.PANEL_ALT, troughcolor=theme.PANEL, arrowcolor=theme.MUTED,
                bordercolor=theme.PANEL, lightcolor=theme.PANEL_ALT, darkcolor=theme.PANEL_ALT)


class DeviceTable(tk.Frame):
    """Incrementally updated table: rows are inserted/updated by key, never rebuilt per event."""

    def __init__(self, parent: tk.Widget, on_select: Callable[[Optional[str]], None]) -> None:
        super().__init__(parent, bg=theme.PANEL)
        self._on_select = on_select
        self._devices: dict[str, Device] = {}
        self._sort_col, self._sort_rev = "distance", False
        self.tree = ttk.Treeview(self, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse")
        for cid, title, width in COLUMNS:
            self.tree.heading(cid, text=title, command=lambda c=cid: self._sort_by(c))
            self.tree.column(cid, width=width, minwidth=50, anchor="w", stretch=cid == "name")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("offline", foreground=theme.OFFLINE)
        self.tree.bind("<<TreeviewSelect>>", self._selected)

    models = {}          # signal source -> PathLossModel, set by the dashboard for the distance column

    @classmethod
    def _values(cls, d: Device) -> tuple:
        cat = d.effective_category.value + (" *" if d.manual_category else "")
        signal = f"{d.rssi:.0f} dBm" if d.rssi is not None else "-"
        return (d.display_name, cat, signal, format_distance(d.distance_m, cls.models.get(d.rssi_source)),
                d.ip or "-", d.mac or "-", d.vendor or "-", d.status.value, fmt_time(d.last_seen))

    def _selected(self, _e=None) -> None:
        sel = self.tree.selection()
        self._on_select(sel[0] if sel else None)

    def upsert_many(self, devices: Iterable[Device]) -> None:
        inserted = False
        for d in devices:
            tags = ("offline",) if d.status is Status.OFFLINE else ()
            if d.key in self._devices:
                self.tree.item(d.key, values=self._values(d), tags=tags)
            else:
                self.tree.insert("", "end", iid=d.key, values=self._values(d), tags=tags)
                inserted = True
            self._devices[d.key] = d
        if inserted:
            self.resort()

    def set_rows(self, devices: Iterable[Device]) -> None:
        """Replace content (used for filter changes and per-cycle refresh)."""
        devices = list(devices)
        wanted = {d.key for d in devices}
        for key in [k for k in self._devices if k not in wanted]:
            self.tree.delete(key)
            del self._devices[key]
        self.upsert_many(devices)
        self.resort()

    def resort(self) -> None:
        keyfn = _SORT[self._sort_col]
        order = sorted(self._devices.values(), key=keyfn, reverse=self._sort_rev)
        for i, d in enumerate(order):
            self.tree.move(d.key, "", i)

    def _sort_by(self, col: str) -> None:
        self._sort_rev = (not self._sort_rev) if col == self._sort_col else False
        self._sort_col = col
        self.resort()

    def select(self, key: str) -> None:
        if key in self._devices:
            self.tree.selection_set(key)
            self.tree.see(key)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._devices.clear()
