"""Compact panel for the BLE mesh-lite emergency relay (see discovery/mesh_relay.py).

Deliberately small and honest: a log of what was heard/sent, and three buttons. No claim of
delivery confirmation or range, because none exists - see the module docstring it is built on.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from discovery.mesh_relay import MeshMessage, MeshMessageType
from ui import theme

_TYPE_LABEL = {MeshMessageType.SOS: "SOS", MeshMessageType.STATUS: "status",
              MeshMessageType.TEXT: "text", MeshMessageType.PING: "ping"}


class MeshPanel(tk.Frame):
    def __init__(self, parent: tk.Widget,
                 on_toggle: Callable[[bool], None],
                 on_send_sos: Callable[[str], None],
                 on_send_status: Callable[[str], None]) -> None:
        super().__init__(parent, bg=theme.PANEL, width=260)
        self.pack_propagate(False)
        self._on_toggle = on_toggle
        self._on_send_sos = on_send_sos
        self._on_send_status = on_send_status

        tk.Label(self, text="Bluetooth mesh relay", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL,
                anchor="w").pack(fill="x", padx=16, pady=(16, 2))
        tk.Label(self, text="No internet/cellular needed. Only reaches other devices running "
                            "this app within Bluetooth range, relayed hop by hop from there. "
                            "Unencrypted - do not send sensitive info.",
                bg=theme.PANEL, fg=theme.OFFLINE, font=theme.FONT_TINY, wraplength=230, justify="left",
                anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        self._enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Enable mesh relay", variable=self._enabled, command=self._toggled,
                      bg=theme.PANEL, fg=theme.TEXT, selectcolor=theme.PANEL, activebackground=theme.PANEL,
                      highlightthickness=0, font=theme.FONT_SMALL, anchor="w").pack(fill="x", padx=16)

        entry_box = tk.Frame(self, bg=theme.PANEL)
        entry_box.pack(fill="x", padx=16, pady=(10, 4))
        self._text = tk.StringVar()
        tk.Entry(entry_box, textvariable=self._text, bg=theme.PANEL_ALT, fg=theme.TEXT,
                 insertbackground=theme.TEXT, relief="flat", font=theme.FONT_SMALL,
                 highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT
                 ).pack(fill="x", ipady=3)
        tk.Label(entry_box, text="max ~14 characters get through - BLE advertising is tiny",
                bg=theme.PANEL, fg=theme.OFFLINE, font=theme.FONT_TINY, anchor="w").pack(fill="x")

        btn_box = tk.Frame(self, bg=theme.PANEL)
        btn_box.pack(fill="x", padx=16, pady=(4, 10))
        self._sos_btn = self._hover_button(btn_box, "Send SOS", self._send_sos, theme.ERROR, "#f3898c")
        self._sos_btn.pack(side="left", padx=(0, 6))
        self._status_btn = self._hover_button(btn_box, "Send status", self._send_status, theme.ACCENT,
                                              theme.ACCENT_HOVER)
        self._status_btn.pack(side="left")

    @staticmethod
    def _hover_button(parent: tk.Widget, text: str, cmd: Callable[[], None], base: str, hover: str) -> tk.Button:
        btn = tk.Button(parent, text=text, command=cmd, bg=base, fg=theme.TEXT, relief="flat", bd=0,
                       padx=14, pady=5, font=theme.FONT_SMALL, cursor="hand2",
                       activebackground=hover, activeforeground=theme.TEXT)
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=base))
        return btn

        tk.Label(self, text="Log", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL,
                anchor="w").pack(fill="x", padx=16, pady=(4, 2))
        self._log = tk.Listbox(self, bg=theme.PANEL_ALT, fg=theme.TEXT, relief="flat", font=theme.FONT_TINY,
                               highlightthickness=0, selectmode="browse", height=12)
        self._log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _toggled(self) -> None:
        self._on_toggle(self._enabled.get())

    def _send_sos(self) -> None:
        self._on_send_sos(self._text.get().strip())
        self._text.set("")

    def _send_status(self) -> None:
        text = self._text.get().strip()
        if text:
            self._on_send_status(text)
            self._text.set("")

    def set_enabled_state(self, enabled: bool) -> None:
        self._enabled.set(enabled)

    def log_sent(self, msg: MeshMessage) -> None:
        self._append(f"-> sent {_TYPE_LABEL.get(msg.msg_type, '?')}: {msg.text or '(no text)'}")

    def log_received(self, mac: str, msg: MeshMessage) -> None:
        kind = _TYPE_LABEL.get(msg.msg_type, "?")
        tag = " [relaying]" if msg.ttl > 0 else " [end of relay chain]"
        self._append(f"<- {kind} from {mac}: {msg.text or '(no text)'}{tag}")

    def log_error(self, text: str) -> None:
        self._append(f"!! {text}")

    def log_info(self, text: str) -> None:
        self._append(f".. {text}")

    def _append(self, line: str) -> None:
        self._log.insert("end", line)
        if self._log.size() > 200:
            self._log.delete(0)
        self._log.see("end")
