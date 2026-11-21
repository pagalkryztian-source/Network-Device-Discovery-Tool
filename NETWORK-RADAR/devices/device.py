from __future__ import annotations

import dataclasses
import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Category(Enum):
    CCTV_CAMERA = "CCTV camera"
    SMARTPHONE = "Smartphone"
    COMPUTER = "Computer"
    SMART_TV = "Smart TV"
    ROUTER = "Router / AP"
    PRINTER = "Printer"
    SMARTWATCH = "Smartwatch"
    IOT_DEVICE = "IoT device"
    NETWORK_STORAGE = "Network storage"
    WIFI_AP = "Wi-Fi network"
    BLUETOOTH = "Bluetooth device"
    UNKNOWN = "Unknown"


class Confidence(Enum):
    HIGH = 3
    MEDIUM = 2
    LOW = 1
    UNKNOWN = 0


class Status(Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"


@dataclass
class Device:
    key: str                                  # MAC when known, else "ip:<addr>"
    ip: Optional[str] = None
    mac: Optional[str] = None
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    category: Category = Category.UNKNOWN     # estimated
    confidence: Confidence = Confidence.UNKNOWN
    reasons: tuple[str, ...] = ()
    manual_category: Optional[Category] = None
    status: Status = Status.ONLINE
    first_seen: float = 0.0
    last_seen: float = 0.0
    missed: int = 0
    transport: str = "ip"
    mdns_services: set[str] = field(default_factory=set)
    ssdp_info: dict[str, str] = field(default_factory=dict)
    open_ports: set[int] = field(default_factory=set)
    bt_name: Optional[str] = None
    sources: set[str] = field(default_factory=set)
    is_self: bool = False
    is_gateway: bool = False
    randomized_mac: bool = False
    last_probe: float = 0.0
    hostname_tried: float = 0.0
    # Passive radio data (Wi-Fi beacons / Bluetooth advertising). None = never measured.
    rssi: Optional[float] = None              # Kalman-smoothed dBm
    rssi_raw: Optional[int] = None            # last raw reading
    rssi_source: Optional[str] = None         # "wifi" | "bluetooth"
    distance_m: Optional[float] = None        # estimate from rssi (see utils/signal.py)
    ssid: Optional[str] = None
    channel: Optional[int] = None
    band: Optional[str] = None
    security: Optional[str] = None
    # Search / track mode (RSSI trend only - no bearing, no GPS of other devices; see utils/signal.py)
    tracked: bool = False
    distance_trend: Optional[str] = None      # "approaching" | "receding" | "steady" | None (not enough data)
    was_close: bool = False                   # latch so the "very close" alert fires once per approach

    @property
    def effective_category(self) -> Category:
        return self.manual_category or self.category

    @property
    def display_name(self) -> str:
        return (self.hostname or self.ssdp_info.get("friendlyName") or self.bt_name or self.ssid
                or (f"{self.vendor} device" if self.vendor else None)
                or self.ip or self.mac or self.key)

    @property
    def ip_sort_key(self) -> int:
        try:
            return int(ipaddress.IPv4Address(self.ip)) if self.ip else -1
        except ValueError:
            return -1

    def snapshot(self) -> "Device":
        """Independent copy that is safe to hand to another thread."""
        return dataclasses.replace(self, mdns_services=set(self.mdns_services),
                                   ssdp_info=dict(self.ssdp_info), open_ports=set(self.open_ports),
                                   sources=set(self.sources))
