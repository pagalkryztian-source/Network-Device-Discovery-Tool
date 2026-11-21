from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from config import defaults as d
from config.settings import Settings
from network.interface_manager import InterfaceInfo
from ui import theme

RADIO_ONLY_LABEL = "Radio only  -  Wi-Fi + Bluetooth, no network needed"
_FLOAT_KEYS = {"scan_interval", "wifi_ref_rssi", "bt_ref_rssi", "path_loss_exp"}


class TopBar(tk.Frame):
    """Brand, mode/interface picker, start/stop, status. Advanced options fold away."""

    def __init__(self, parent: tk.Widget, settings: Settings, on_start: Callable[[], None],
                 on_stop: Callable[[], None], on_interface: Callable[[], None]) -> None:
        super().__init__(parent, bg=theme.PANEL)
        self._ifaces: list[InterfaceInfo] = []
        row = tk.Frame(self, bg=theme.PANEL)
        row.pack(fill="x", padx=20, pady=14)
        tk.Label(row, text="Network Radar", bg=theme.PANEL, fg=theme.TEXT, font=theme.FONT_BRAND
                 ).pack(side="left", padx=(0, 24))
        self._iface = ttk.Combobox(row, state="readonly", width=44)
        self._iface.pack(side="left")
        self._iface.bind("<<ComboboxSelected>>", lambda _e: on_interface())
        tk.Label(row, text="Subnet", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL).pack(side="left", padx=(16, 6))
        self._subnet = tk.Entry(row, width=18, bg=theme.PANEL_ALT, fg=theme.TEXT, insertbackground=theme.TEXT,
                                relief="flat", font=theme.FONT, highlightthickness=1,
                                highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        self._subnet.pack(side="left", ipady=3)

        self._toggle = self._button(row, "Settings", self._toggle_settings)
        self._toggle.pack(side="right", padx=(8, 0))
        self._start = self._button(row, "Start", on_start, primary=True)
        self._start.pack(side="right", padx=(8, 0))
        self._stop = self._button(row, "Stop", on_stop)
        self._stop.pack(side="right", padx=(8, 0))
        self._status = tk.Label(row, text="Idle", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT)
        self._status.pack(side="right", padx=(0, 18))

        self._adv = tk.Frame(self, bg=theme.PANEL)
        self._adv_visible = False
        self._vars: dict[str, tk.Variable] = {}
        self._flags: dict[str, tk.BooleanVar] = {}
        line1 = tk.Frame(self._adv, bg=theme.PANEL)
        line1.pack(fill="x", padx=20, pady=(0, 6))
        line2 = tk.Frame(self._adv, bg=theme.PANEL)
        line2.pack(fill="x", padx=20, pady=(0, 12))
        for label, key, lo, hi, val, step in (
                ("Interval (s)", "scan_interval", d.MIN_SCAN_INTERVAL_S, d.MAX_SCAN_INTERVAL_S, settings.scan_interval, 5),
                ("Missed before offline", "missed_threshold", 1, 100, settings.missed_threshold, 1),
                ("ARP pkts/s", "packet_rate", 1, d.MAX_PACKET_RATE, settings.packet_rate, 10),
                ("Concurrency", "concurrency", 1, d.MAX_CONCURRENCY, settings.concurrency, 1)):
            self._spin(line1, label, key, lo, hi, val, step)
        for label, key, val in (("Wi-Fi", "enable_wifi", settings.enable_wifi),
                                ("Bluetooth", "enable_bluetooth", settings.enable_bluetooth),
                                ("mDNS", "enable_mdns", settings.enable_mdns),
                                ("SSDP", "enable_ssdp", settings.enable_ssdp),
                                ("Hostnames", "enable_hostname", settings.enable_hostname),
                                ("Service probe", "enable_probe", settings.enable_probe)):
            var = tk.BooleanVar(value=val)
            tk.Checkbutton(line2, text=label, variable=var, bg=theme.PANEL, fg=theme.TEXT, selectcolor=theme.PANEL,
                           activebackground=theme.PANEL, activeforeground=theme.TEXT, font=theme.FONT_SMALL,
                           highlightthickness=0, bd=0).pack(side="left", padx=(0, 14))
            self._flags[key] = var
        tk.Label(line2, text="Calibration", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL).pack(side="left", padx=(18, 8))
        for label, key, lo, hi, val, step in (
                ("Wi-Fi @1 m dBm", "wifi_ref_rssi", -80, -10, settings.wifi_ref_rssi, 1),
                ("BT @1 m dBm", "bt_ref_rssi", -100, -20, settings.bt_ref_rssi, 1),
                ("Path-loss n", "path_loss_exp", 1.5, 5.0, settings.path_loss_exp, 0.1)):
            self._spin(line2, label, key, lo, hi, val, step)
        tk.Label(line2, text="Mesh relay hops", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL
                ).pack(side="left", padx=(18, 8))
        self._spin(line2, "Max TTL", "mesh_ttl", 1, 16, settings.mesh_ttl, 1)
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x", side="bottom")
        self.set_scanning(False)

    def _spin(self, parent: tk.Widget, label: str, key: str, lo: float, hi: float, val: float, step: float) -> None:
        tk.Label(parent, text=label, bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL).pack(side="left", padx=(0, 4))
        var = tk.StringVar(value=(f"{val:g}" if key in _FLOAT_KEYS else str(int(val))))
        ttk.Spinbox(parent, from_=lo, to=hi, increment=step, width=6, textvariable=var).pack(side="left", padx=(0, 14))
        self._vars[key] = var

    @staticmethod
    def _button(parent: tk.Widget, text: str, cmd: Callable[[], None], primary: bool = False) -> tk.Button:
        base = theme.ACCENT if primary else theme.PANEL_ALT
        hover = theme.ACCENT_HOVER if primary else theme.PANEL_HOVER
        btn = tk.Button(parent, text=text, command=cmd, relief="flat", bd=0, padx=16, pady=6, font=theme.FONT_SMALL,
                       bg=base, fg=theme.TEXT, activebackground=hover, activeforeground=theme.TEXT,
                       disabledforeground=theme.OFFLINE, cursor="hand2")
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover) if str(btn["state"]) != "disabled" else None)
        btn.bind("<Leave>", lambda _e: btn.configure(bg=base) if str(btn["state"]) != "disabled" else None)
        return btn

    def _toggle_settings(self) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv.pack(fill="x", before=self.winfo_children()[-1])
        else:
            self._adv.pack_forget()

    # ---- API used by the dashboard
    def set_interfaces(self, ifaces: list[InterfaceInfo]) -> None:
        self._ifaces = ifaces
        self._iface["values"] = [RADIO_ONLY_LABEL] + [i.label for i in ifaces]
        if not self._iface.get():
            self._iface.current(1 if ifaces else 0)       # no network available -> radio-only by default

    def radio_only(self) -> bool:
        return self._iface.current() <= 0

    def selected_interface(self) -> InterfaceInfo | None:
        idx = self._iface.current() - 1
        return self._ifaces[idx] if 0 <= idx < len(self._ifaces) else None

    def show_interface(self, iface: InterfaceInfo | None) -> None:
        self._subnet.configure(state="normal")
        self._subnet.delete(0, "end")
        if iface:
            self._subnet.insert(0, str(iface.network))
        else:
            self._subnet.configure(state="disabled")

    def subnet_text(self) -> str:
        return self._subnet.get().strip()

    def settings_values(self) -> dict:
        values: dict = {}
        for key, var in self._vars.items():
            raw = str(var.get()).strip()
            values[key] = float(raw) if key in _FLOAT_KEYS else int(float(raw))
        for key, var in self._flags.items():
            values[key] = bool(var.get())
        return values

    def set_scanning(self, scanning: bool) -> None:
        self._start.configure(state="disabled" if scanning else "normal")
        self._stop.configure(state="normal" if scanning else "disabled")
        self._iface.configure(state="disabled" if scanning else "readonly")
        self._subnet.configure(state="disabled" if scanning or self.radio_only() else "normal")
        self.set_status("Scanning" if scanning else "Idle", theme.OK if scanning else theme.MUTED)

    def set_status(self, text: str, color: str) -> None:
        self._status.configure(text="●  " + text, fg=color)
