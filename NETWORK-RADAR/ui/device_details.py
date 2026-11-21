from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from devices.device import Category, Device
from network.network_utils import port_label
from ui import theme
from utils.signal import format_distance, signal_quality
from utils.timestamps import fmt

_AUTO = "(automatic)"


class DeviceDetailsPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, on_override: Callable[[str, Optional[Category]], None],
                 model_for=lambda source: None,
                 on_track: Optional[Callable[[str, bool], None]] = None) -> None:
        super().__init__(parent, bg=theme.PANEL, width=290)
        self._model_for = model_for
        self.pack_propagate(False)
        self._on_override = on_override
        self._on_track = on_track
        self._key: Optional[str] = None
        tk.Label(self, text="Details", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL,
                 anchor="w").pack(fill="x", padx=20, pady=(20, 6))
        self._title = tk.Label(self, text="Select a device", bg=theme.PANEL, fg=theme.TEXT,
                               font=theme.FONT_TITLE, anchor="w", wraplength=270, justify="left")
        self._title.pack(fill="x", padx=20)
        self._text = tk.Text(self, bg=theme.PANEL, fg=theme.TEXT, font=theme.FONT_SMALL, relief="flat",
                             wrap="word", height=10, spacing1=2, spacing3=3, highlightthickness=0, state="disabled", cursor="arrow")
        self._text.tag_configure("k", foreground=theme.MUTED)
        self._text.tag_configure("h", foreground=theme.ACCENT)
        self._text.pack(fill="both", expand=True, padx=20, pady=10)

        box = tk.Frame(self, bg=theme.PANEL)
        box.pack(fill="x", padx=20, pady=(0, 20))
        tk.Label(box, text="Correct the type", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL).pack(anchor="w")
        self._combo = ttk.Combobox(box, state="readonly", values=[_AUTO] + [c.value for c in Category])
        self._combo.set(_AUTO)
        self._combo.pack(fill="x", pady=4)
        self._combo.bind("<<ComboboxSelected>>", self._changed)

        track_box = tk.Frame(self, bg=theme.PANEL)
        track_box.pack(fill="x", padx=20, pady=(0, 12))
        self._track_var = tk.BooleanVar(value=False)
        self._track_check = tk.Checkbutton(
            track_box, text="Track this device (search mode)", variable=self._track_var,
            command=self._track_changed, bg=theme.PANEL, fg=theme.TEXT, selectcolor=theme.PANEL,
            activebackground=theme.PANEL, highlightthickness=0, font=theme.FONT_SMALL, anchor="w")
        self._track_check.pack(fill="x")
        tk.Label(track_box, text="Alerts you when it gets very close, goes offline, or reappears. "
                                 "Shows approaching/receding from signal strength - not a GPS position.",
                bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL, wraplength=270, justify="left",
                anchor="w").pack(fill="x", pady=(2, 0))

    def _track_changed(self) -> None:
        if self._key and self._on_track:
            self._on_track(self._key, self._track_var.get())

    def _changed(self, _e=None) -> None:
        if not self._key:
            return
        value = self._combo.get()
        cat = None if value == _AUTO else next(c for c in Category if c.value == value)
        self._on_override(self._key, cat)

    @property
    def current_key(self) -> Optional[str]:
        return self._key

    def show(self, d: Optional[Device]) -> None:
        self._key = d.key if d else None
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        if d is None:
            self._title.configure(text="Select a device")
            self._combo.set(_AUTO)
            self._track_var.set(False)
            self._track_check.configure(state="disabled")
        else:
            self._track_check.configure(state="normal")
            self._track_var.set(d.tracked)
            self._title.configure(text=d.display_name)
            self._combo.set(d.manual_category.value if d.manual_category else _AUTO)
            cat = d.effective_category.value + (" (manual)" if d.manual_category else "")
            conf = "manual" if d.manual_category else d.confidence.name
            rows = [("IP address", d.ip or "-"), ("MAC address", d.mac or "-"),
                    ("Manufacturer", d.vendor or ("randomised MAC" if d.randomized_mac else "-")),
                    ("Hostname", d.hostname or "-"), ("Category", cat), ("Confidence", conf),
                    ("Status", d.status.value), ("First detected", fmt(d.first_seen)),
                    ("Last detected", fmt(d.last_seen))]
            if d.bt_name:
                rows.insert(4, ("Bluetooth name", d.bt_name))
            for k, v in rows:
                if v == "-":
                    continue                                  # show only what is known
                self._text.insert("end", f"{k}  ", "k")
                self._text.insert("end", f"{v}\n")
            if d.rssi is not None:
                self._section("Signal")
                model = self._model_for(d.rssi_source)
                lines = [f"Strength  {d.rssi:.0f} dBm ({signal_quality(d.rssi)})"]
                if d.rssi_raw is not None:
                    lines.append(f"Last raw reading  {d.rssi_raw} dBm")
                lines.append(f"Distance  {format_distance(d.distance_m, model)}")
                if d.distance_trend:
                    arrow = {"approaching": "getting closer \u2193", "receding": "moving away \u2191",
                            "steady": "holding steady \u2192"}.get(d.distance_trend, d.distance_trend)
                    lines.append(f"Trend  {arrow}")
                elif d.tracked:
                    lines.append("Trend  not enough readings yet")
                if d.ssid is not None or d.channel:
                    lines.append(f"Network  {d.ssid or '(hidden)'}  ch {d.channel or '?'}  {d.band or ''}  {d.security or ''}".rstrip())
                lines.append("Direction  not measurable with a single receiver")
                self._text.insert("end", "\n".join(lines) + "\n")
            self._section("Why this type")
            for reason in d.reasons:
                self._text.insert("end", f"• {reason}\n")
            self._section("Discovered services")
            lines = [f"mDNS  {s}" for s in sorted(d.mdns_services)]
            lines += [f"port  {port_label(p)}" for p in sorted(d.open_ports)]
            info = d.ssdp_info
            for k, label in (("friendlyName", "UPnP name"), ("modelName", "UPnP model"),
                             ("manufacturer", "UPnP vendor"), ("server", "UPnP server")):
                if info.get(k):
                    lines.append(f"{label}  {info[k]}")
            self._text.insert("end", ("\n".join(lines) if lines else "none observed") + "\n")
        self._text.configure(state="disabled")

    def _section(self, title: str) -> None:
        self._text.insert("end", f"\n{title}\n", "h")
