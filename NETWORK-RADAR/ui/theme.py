from __future__ import annotations

from devices.device import Category as C

# Dark theme: charcoal grays, a forest-green accent, white/near-white text.
# Every widget in ui/ reads its colors from here - change a value once and the whole app follows.
BG = "#17191c"            # app background - deep charcoal, not pure black (easier on the eyes)
PANEL = "#1e2125"          # cards / panels - one step lighter than BG so surfaces read as "raised"
PANEL_ALT = "#282c31"      # inputs, alternating rows, anything that should sit "inside" a panel
PANEL_HOVER = "#30353b"    # hover state for panel-colored buttons/rows
BORDER = "#34393f"         # hairline borders between regions
GRID = "#2b3430"           # radar range rings / spokes - a faint green-gray, not plain gray
TEXT = "#f3f5f4"           # primary text - soft white, not stark #fff (less glare on dark bg)
MUTED = "#9aa3a0"          # secondary text, labels, timestamps
ACCENT = "#2e8b57"         # dark/forest green - primary brand & selection color
ACCENT_HOVER = "#379d63"   # accent, one step brighter for hover states
ACCENT_SOFT = "#1d3327"    # accent tinted into the dark palette - used for selected-row backgrounds
WARN = "#d9a441"
ERROR = "#e5676b"
OK = "#3ecf8e"             # brighter green than ACCENT so "online" status reads distinctly
OFFLINE = "#5b6268"
FONT = ("DejaVu Sans", 10)
FONT_SMALL = ("DejaVu Sans", 9)
FONT_TINY = ("DejaVu Sans", 8)
FONT_TITLE = ("DejaVu Sans", 13, "bold")
FONT_BRAND = ("DejaVu Sans", 12, "bold")

# category -> (marker colour, marker shape) - kept saturated so they stay legible on a dark canvas
MARKERS: dict[C, tuple[str, str]] = {
    C.CCTV_CAMERA: ("#f0656a", "triangle"),
    C.SMARTPHONE: ("#4dabf7", "tall"),
    C.COMPUTER: ("#9775fa", "square"),
    C.SMART_TV: ("#e667af", "wide"),
    C.ROUTER: ("#2ee6b8", "diamond"),
    C.PRINTER: ("#ff9248", "hexagon"),
    C.SMARTWATCH: ("#ffb52e", "circle"),
    C.IOT_DEVICE: ("#51cf66", "circle"),
    C.NETWORK_STORAGE: ("#c99a7f", "pentagon"),
    C.WIFI_AP: ("#3bc9db", "diamond"),
    C.BLUETOOTH: ("#5c7cfa", "circle"),
    C.UNKNOWN: ("#9aa3a0", "circle"),
}

# Sidebar groups: label -> categories
SIDEBAR_GROUPS: list[tuple[str, frozenset[C]]] = [
    ("Wi-Fi networks", frozenset({C.WIFI_AP})),
    ("Bluetooth", frozenset({C.BLUETOOTH, C.SMARTWATCH})),
    ("Smartphones", frozenset({C.SMARTPHONE})),
    ("Computers", frozenset({C.COMPUTER})),
    ("Routers", frozenset({C.ROUTER})),
    ("Smart TVs", frozenset({C.SMART_TV})),
    ("CCTV", frozenset({C.CCTV_CAMERA})),
    ("Printers", frozenset({C.PRINTER})),
    ("IoT", frozenset({C.IOT_DEVICE})),
    ("Other", frozenset({C.NETWORK_STORAGE})),
    ("Unknown", frozenset({C.UNKNOWN})),
]
