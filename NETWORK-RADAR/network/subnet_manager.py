from __future__ import annotations

import ipaddress
from typing import Iterable, Iterator, Optional

from network.interface_manager import InterfaceInfo
from utils.validators import parse_subnet, validate_scan_scope


class SubnetManager:
    @staticmethod
    def resolve_scope(interface: InterfaceInfo, subnet_text: Optional[str],
                      max_hosts: int) -> ipaddress.IPv4Network:
        """Return the validated scan scope (defaults to the interface's own network)."""
        network = parse_subnet(subnet_text) if subnet_text and subnet_text.strip() else interface.network
        validate_scan_scope(network, interface.network, max_hosts)
        return network

    @staticmethod
    def iter_hosts(network: ipaddress.IPv4Network, exclude: Iterable[str] = ()) -> Iterator[str]:
        skip = set(exclude)
        for host in network.hosts():
            s = str(host)
            if s not in skip:
                yield s
