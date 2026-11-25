"""Small socket/OS helpers (no scanning logic here)."""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import struct
import threading
from typing import Iterable, Optional

from config.defaults import PORT_NAMES
from utils.rate_limiter import RateLimiter

log = logging.getLogger(__name__)
CAP_NET_RAW_BIT = 13


def is_locally_administered(mac: str) -> bool:
    """True if the 'locally administered' bit is set (randomised/private MACs, VMs)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def get_default_gateway(iface: str, route_file: str = "/proc/net/route") -> Optional[str]:
    try:
        with open(route_file, encoding="ascii") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) < 4 or parts[0] != iface:
                    continue
                if parts[1] == "00000000" and int(parts[3], 16) & 0x2:
                    return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
    except (OSError, ValueError) as exc:
        log.warning("Could not read default gateway: %s", exc)
    return None


def has_raw_socket_privilege(status_file: str = "/proc/self/status") -> bool:
    """True if root or holding CAP_NET_RAW (needed for ARP via Scapy)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    try:
        with open(status_file, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) & (1 << CAP_NET_RAW_BIT))
    except (OSError, ValueError):
        pass
    return False


def ip_in_network(ip: str, network: ipaddress.IPv4Network) -> bool:
    try:
        return ipaddress.ip_address(ip) in network
    except ValueError:
        return False


def resolve_hostname(ip: str) -> Optional[str]:
    """Reverse DNS (and system resolver, e.g. nss-mdns/NetBIOS if configured)."""
    try:
        name = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None
    name = name.rstrip(".")
    return name or None


def probe_ports(ip: str, ports: Iterable[int], timeout: float,
                limiter: Optional[RateLimiter] = None,
                cancel: Optional[threading.Event] = None) -> list[int]:
    """Plain TCP connect checks of a small fixed port list (no payloads, no banner grabbing)."""
    open_ports: list[int] = []
    for port in ports:
        if cancel is not None and cancel.is_set():
            break
        if limiter is not None:
            limiter.acquire(cancel)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                if s.connect_ex((ip, port)) == 0:
                    open_ports.append(port)
            except OSError:
                continue
    return open_ports


def port_label(port: int) -> str:
    name = PORT_NAMES.get(port)
    return f"{port}/{name}" if name else str(port)
