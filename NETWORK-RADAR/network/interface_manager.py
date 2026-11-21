from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Any, Optional

import psutil

log = logging.getLogger(__name__)


class InterfaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class InterfaceInfo:
    name: str
    ip: str
    netmask: str
    mac: Optional[str]
    is_up: bool

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.ip}/{self.netmask}", strict=False)  # type: ignore[return-value]

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.ip})"


class NetworkInterfaceManager:
    """Enumerates usable IPv4 interfaces. psutil is injectable for tests."""

    def __init__(self, psutil_module: Any = psutil) -> None:
        self._ps = psutil_module

    def list_interfaces(self) -> list[InterfaceInfo]:
        try:
            addrs = self._ps.net_if_addrs()
            stats = self._ps.net_if_stats()
        except Exception as exc:  # psutil raises various OS errors
            raise InterfaceError(f"Unable to read network interfaces: {exc}") from exc
        link_family = getattr(self._ps, "AF_LINK", None)
        result: list[InterfaceInfo] = []
        for name, entries in addrs.items():
            ipv4 = next((e for e in entries if e.family == socket.AF_INET and e.address and e.netmask), None)
            if ipv4 is None:
                continue
            try:
                if ipaddress.IPv4Address(ipv4.address).is_loopback:
                    continue
            except ValueError:
                continue
            mac = next((e.address for e in entries if link_family is not None and e.family == link_family), None)
            st = stats.get(name)
            result.append(InterfaceInfo(name, ipv4.address, ipv4.netmask, mac, bool(st and st.isup)))
        return sorted(result, key=lambda i: (not i.is_up, i.name))

    def get(self, name: str) -> InterfaceInfo:
        for iface in self.list_interfaces():
            if iface.name == name:
                if not iface.is_up:
                    raise InterfaceError(f"Interface {name} is down.")
                return iface
        raise InterfaceError(f"Interface {name!r} not found or has no IPv4 address.")
