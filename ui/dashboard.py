from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Optional

from config import defaults as d
from core.application import ApplicationController
from core.event_manager import Event, EventType
from devices.device import Category, Device
from network.interface_manager import InterfaceError
from network.subnet_manager import SubnetManager
from ui import theme
from ui.components.top_bar import TopBar
from ui.device_details import DeviceDetailsPanel
from ui.device_table import DeviceTable, style_ttk
from ui.radar import RadarRenderer
from ui.sidebar import CategorySidebar
from utils.validators import ValidationError

log = logging.getLogger(__name__)
_DEVICE_EVENTS = {EventType.DEVICE_DISCOVERED, EventType.DEVICE_UPDATED, EventType.DEVICE_OFFLINE,
                  EventType.DEVICE_REAPPEARED, EventType.CLASSIFICATION_UPDATED}


class Dashboard:
    def __init__(self, root: tk.Tk, controller: ApplicationController) -> None:
        self.root, self.ctl = root, controller
        self._filter: Optional[frozenset[Category]] = None
        self._issues: dict[str, str] = {}          # non-fatal module problems of the current cycle
        root.title(d.APP_NAME)
        root.geometry("1480x880")
        root.minsize(1100, 680)
        root.configure(bg=theme.BG)
        style_ttk(root)

        self.top = TopBar(root, controller.settings, self._start, self._stop, self._interface_changed)
        self.top.pack(fill="x")

        self.statusbar = tk.Label(root, text="", anchor="w", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL,
                                  pady=6, padx=20)
        self.statusbar.pack(fill="x", side="bottom")
        tk.Frame(root, bg=theme.BORDER, height=1).pack(fill="x", side="bottom")

        body = tk.Frame(root, bg=theme.BG)
        body.pack(fill="both", expand=True)
        self.sidebar = CategorySidebar(body, self._filter_changed)
        self.sidebar.pack(side="left", fill="y")
        tk.Frame(body, bg=theme.BORDER, width=1).pack(side="right", fill="y")
        self.details = DeviceDetailsPanel(body, self._override, self.ctl.registry.path_model, self._track)
        self.details.pack(side="right", fill="y")

        centre = tk.PanedWindow(body, orient="vertical", bg=theme.BG, sashwidth=4, bd=0)
        centre.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(centre, height=460, width=600)
        self.radar = RadarRenderer(canvas, on_click=self._radar_clicked)
        centre.add(canvas, stretch="always", minsize=240)
        table_box = tk.Frame(centre, bg=theme.PANEL)
        tk.Frame(table_box, bg=theme.BORDER, height=1).pack(fill="x")
        search_row = tk.Frame(table_box, bg=theme.PANEL)
        search_row.pack(fill="x")
        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_table())
        tk.Entry(search_row, textvariable=self._search, bg=theme.PANEL_ALT, fg=theme.TEXT, insertbackground=theme.TEXT,
                 relief="flat", font=theme.FONT, width=40, highlightthickness=1, highlightbackground=theme.BORDER,
                 highlightcolor=theme.ACCENT).pack(side="left", padx=20, pady=10, ipady=3)
        tk.Label(search_row, text="Search name, IP, MAC, SSID or manufacturer", bg=theme.PANEL, fg=theme.OFFLINE,
                 font=theme.FONT_SMALL).pack(side="left")
        self.table = DeviceTable(table_box, self._table_selected)
        self.table.pack(fill="both", expand=True)
        centre.add(table_box, stretch="never", minsize=180)


        self._load_interfaces()
        if not controller.raw_privileges_ok():
            self._note("No raw-socket privilege: network (ARP) scanning is unavailable; radio-only mode still works.", theme.WARN)
        else:
            self._note("Ready. Pick a mode and press Start. Radio-only mode needs no network.")
        self.radar.start()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(d.GUI_POLL_MS, self._poll)

    # ------------------------------------------------------------ controls
    def _note(self, text: str, color: str = theme.MUTED) -> None:
        self.statusbar.configure(text=text, fg=color)

    def _load_interfaces(self) -> None:
        try:
            ifaces = self.ctl.list_interfaces()
        except InterfaceError as exc:
            messagebox.showerror("Network interfaces", str(exc))
            ifaces = []
        self.top.set_interfaces(ifaces)
        self._interface_changed()
        if not ifaces:
            self._note("No network interface found. Radio-only mode (Wi-Fi + Bluetooth) is available.", theme.MUTED)

    def _interface_changed(self) -> None:
        self.top.show_interface(self.top.selected_interface())

    def _start(self) -> None:
        if self.top.radio_only():
            try:
                self.ctl.apply_settings(**self.top.settings_values())
                self.ctl.start_radio()
            except (ValidationError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot start", str(exc))
                return
            self._sync_models()
            self.top.set_scanning(True)
            self._note("Listening for Wi-Fi networks and Bluetooth devices (passive, no network needed)")
            return
        iface = self.top.selected_interface()
        try:
            self.ctl.apply_settings(**self.top.settings_values())
            subnet = self.top.subnet_text()
            scope = SubnetManager.resolve_scope(iface, subnet, self.ctl.settings.max_subnet_hosts)
        except (ValidationError, ValueError) as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return
        if not messagebox.askyesno(
                "Authorisation required",
                f"You are about to actively probe {scope} through {iface.name}.\n\n"
                "Only continue if you own this network or have explicit permission to monitor it.\n\n"
                "Do you confirm you are authorised?", icon="warning"):
            return
        try:
            self.ctl.start_scan(iface.name, str(scope), authorized=True)
        except (ValidationError, InterfaceError, RuntimeError, PermissionError) as exc:
            messagebox.showerror("Cannot start scan", str(exc))
            return
        self._sync_models()
        self.top.set_scanning(True)
        self._note(f"Scanning {scope} on {iface.name}")

    def _sync_models(self) -> None:
        DeviceTable.models = {src: self.ctl.registry.path_model(src) for src in ("wifi", "bluetooth")}

    def _stop(self) -> None:
        self._note("Stopping...")
        # join() can take a couple of seconds (current ARP batch); keep the GUI responsive.
        threading.Thread(target=self.ctl.stop_scan, name="stop-scan", daemon=True).start()

    def _filter_changed(self, cats: Optional[frozenset[Category]]) -> None:
        self._filter = cats
        self.radar.set_filter(cats)
        self._refresh_table()

    def _refresh_table(self) -> None:
        self.table.set_rows(self.ctl.registry.query(self._filter, self._search.get()))

    def _table_selected(self, key: Optional[str]) -> None:
        self.details.show(self.ctl.registry.get(key) if key else None)

    def _radar_clicked(self, key: str) -> None:
        self.table.select(key)

    def _override(self, key: str, cat: Optional[Category]) -> None:
        dev = self.ctl.set_manual_category(key, cat)
        if dev:
            self._apply_devices({dev.key: dev}, set())
            self.details.show(dev)
            self.sidebar.set_counts(self.ctl.registry.counts())

    def _track(self, key: str, tracked: bool) -> None:
        dev = self.ctl.set_tracked(key, tracked)
        if dev:
            self._apply_devices({dev.key: dev}, set())
            self.details.show(dev)

    # ------------------------------------------------------------ event loop
    def _poll(self) -> None:
        try:
            events = self.ctl.poll_events()
            if events:
                self._handle(events)
        except Exception:
            log.exception("GUI event handling failed")
        self.root.after(d.GUI_POLL_MS, self._poll)

    def _handle(self, events: list[Event]) -> None:
        changed: dict[str, Device] = {}
        new_keys: set[str] = set()
        refresh_all = False
        for ev in events:
            t = ev.type
            if t in _DEVICE_EVENTS:
                dev: Device = ev.payload["device"]
                changed[dev.key] = dev
                if t is EventType.DEVICE_DISCOVERED:
                    new_keys.add(dev.key)
            elif t is EventType.SCAN_STARTED:
                self.top.set_scanning(True)
                self._note(f"Scanning {ev.payload.get('network')} on {ev.payload.get('interface')}")
            elif t is EventType.SCAN_STOPPED:
                self.top.set_scanning(False)
                self._note("Scanning stopped.")
            elif t is EventType.CYCLE_STARTED:
                self._issues.clear()
            elif t is EventType.CYCLE_COMPLETED:
                refresh_all = True
                self._sync_models()
                text = (f"Last scan {ev.payload['duration']:.1f}s  -  {ev.payload['online']} online, "
                        f"{ev.payload['total']} seen since start")
                if self._issues:                    # keep the reason visible instead of hiding it
                    self._note(text + "   |   " + "   |   ".join(self._issues.values()), theme.WARN)
                else:
                    self._note(text)
            elif t is EventType.SCAN_ERROR:
                self._issues[ev.payload.get("source", "scan")] = ev.payload["message"]
                self._note(ev.payload["message"], theme.ERROR)
                if ev.payload.get("fatal"):
                    messagebox.showerror("Scan error", ev.payload["message"])
            elif t is EventType.TARGET_ALERT:
                dev: Device = ev.payload["device"]
                changed[dev.key] = dev
                self._target_alert(dev, ev.payload.get("reason", ""))
        if changed:
            self._apply_devices(changed, new_keys)
        if refresh_all:
            self._refresh_table()                      # refresh "last seen" for every row (O(n) once per cycle)
        if changed or refresh_all:
            self.sidebar.set_counts(self.ctl.registry.counts())
            key = self.details.current_key
            if key:
                self.details.show(self.ctl.registry.get(key))

    def _target_alert(self, dev: Device, reason: str) -> None:
        name = dev.display_name
        if reason == "very_close":
            msg = f"Tracked target very close: {name} (~{dev.distance_m:.1f} m estimate, +/-50% typical error)"
            self._note(msg, theme.WARN)
        elif reason == "lost":
            msg = f"Tracked target lost: {name} stopped responding"
            self._note(msg, theme.ERROR)
            messagebox.showwarning("Target lost", msg)
        elif reason == "reappeared":
            msg = f"Tracked target reappeared: {name}"
            self._note(msg, theme.WARN)
        else:
            return

    def _apply_devices(self, changed: dict[str, Device], new_keys: set[str]) -> None:
        rows = []
        text = self._search.get().strip().lower()
        for key, dev in changed.items():
            self.radar.upsert(dev, announce=key in new_keys)
            if self._filter is not None and dev.effective_category not in self._filter:
                continue
            if text and not any(text in (v or "").lower() for v in (dev.ip, dev.mac, dev.hostname, dev.vendor, dev.display_name)):
                continue
            rows.append(dev)
        self.table.upsert_many(rows)

    def _close(self) -> None:
        self.radar.stop()
        self.ctl.shutdown()
        self.root.destroy()
