from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from devices.device import Category
from ui import theme


class CategorySidebar(tk.Frame):
    """Quiet list of live counters. Clicking a row filters the table and radar."""

    def __init__(self, parent: tk.Widget, on_select: Callable[[Optional[frozenset[Category]]], None]) -> None:
        super().__init__(parent, bg=theme.BG, width=200)
        self.pack_propagate(False)
        self._on_select = on_select
        self._rows: list[tuple[Optional[frozenset[Category]], tk.Frame, tk.Frame, tk.Label, tk.Label]] = []
        self._selected: Optional[frozenset[Category]] = None
        tk.Label(self, text="Filter", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL,
                 anchor="w").pack(fill="x", padx=20, pady=(20, 8))
        self._add_row("All devices", None)
        for label, cats in theme.SIDEBAR_GROUPS:
            self._add_row(label, cats)
        self._paint()

    def _add_row(self, label: str, cats: Optional[frozenset[Category]]) -> None:
        row = tk.Frame(self, bg=theme.BG, cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        bar = tk.Frame(row, bg=theme.BG, width=3)
        bar.pack(side="left", fill="y")
        name = tk.Label(row, text=label, bg=theme.BG, fg=theme.TEXT, font=theme.FONT, anchor="w")
        name.pack(side="left", padx=(9, 0), pady=6)
        count = tk.Label(row, text="0", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="e")
        count.pack(side="right", padx=10)
        for w in (row, bar, name, count):
            w.bind("<Button-1>", lambda _e, c=cats: self._click(c))
        row.bind("<Enter>", lambda _e, c=cats: self._hover(c, True))
        row.bind("<Leave>", lambda _e, c=cats: self._hover(c, False))
        self._rows.append((cats, row, bar, name, count))

    def _click(self, cats: Optional[frozenset[Category]]) -> None:
        self._selected = cats
        self._paint()
        self._on_select(cats)

    def _hover(self, cats: Optional[frozenset[Category]], entering: bool) -> None:
        if cats == self._selected:
            return
        for c, row, bar, name, count in self._rows:
            if c == cats:
                bg = theme.PANEL if entering else theme.BG
                for w in (row, bar, name, count):
                    w.configure(bg=bg)

    def _paint(self) -> None:
        for cats, row, bar, name, count in self._rows:
            active = cats == self._selected
            bg = theme.ACCENT_SOFT if active else theme.BG
            for w in (row, name, count):
                w.configure(bg=bg)
            bar.configure(bg=theme.ACCENT if active else bg)
            name.configure(fg=theme.TEXT, font=(theme.FONT[0], theme.FONT[1], "bold") if active else theme.FONT)
            count.configure(fg=theme.ACCENT_HOVER if active else theme.MUTED)

    def set_counts(self, counts: dict[Category, int]) -> None:
        total = 0
        for cats, _row, _bar, _name, lbl in self._rows:
            if cats is None:
                continue
            n = sum(counts.get(c, 0) for c in cats)
            total += n
            lbl.configure(text=str(n))
        for cats, _row, _bar, _name, lbl in self._rows:
            if cats is None:
                lbl.configure(text=str(total))
