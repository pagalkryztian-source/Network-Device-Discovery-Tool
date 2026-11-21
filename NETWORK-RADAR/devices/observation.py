"""A single piece of evidence reported by a discovery module or enrichment step."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Observation:
    source: str                              # "arp", "mdns", "ssdp", "bluetooth", "wifi", "hostname", "probe"
    ip: Optional[str] = None
    mac: Optional[str] = None
    hostname: Optional[str] = None
    mdns_services: set[str] = field(default_factory=set)
    ssdp: dict[str, str] = field(default_factory=dict)
    open_ports: Optional[set[int]] = None    # None = not probed; empty set = probed, none open
    bt_name: Optional[str] = None
    transport: str = "ip"                    # "ip" | "bluetooth" | "wifi"
    rssi: Optional[int] = None               # dBm, passive radio sources only
    ssid: Optional[str] = None
    channel: Optional[int] = None
    band: Optional[str] = None
    security: Optional[str] = None
    is_self: bool = False
    is_gateway: bool = False
    # Reverse-DNS or a port probe does not prove a host is currently present.
    confirms_presence: bool = True
