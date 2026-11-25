"""Input validation. All scan-scope safety rules live here."""
from __future__ import annotations

import ipaddress
import re
from typing import Optional

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


class ValidationError(ValueError):
    """Raised for invalid user input or an unsafe scan scope."""


def normalize_mac(value: Optional[str]) -> Optional[str]:
    """Return 'aa:bb:cc:dd:ee:ff' or None if not a usable unicast MAC."""
    if not value:
        return None
    raw = re.sub(r"[:\-.\s]", "", str(value)).lower()
    if not _MAC_RE.match(raw):
        return None
    if raw in ("000000000000", "ffffffffffff"):
        return None
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def parse_ipv4(value: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"Invalid IPv4 address: {value!r}") from exc


def parse_subnet(text: str) -> ipaddress.IPv4Network:
    """Parse CIDR text. Host bits are tolerated (192.168.1.5/24 -> 192.168.1.0/24)."""
    text = (text or "").strip()
    if not text:
        raise ValidationError("Subnet is empty. Use CIDR notation, e.g. 192.168.1.0/24.")
    if "/" not in text:
        raise ValidationError("Subnet must use CIDR notation, e.g. 192.168.1.0/24.")
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ValidationError(f"Invalid subnet {text!r}: {exc}") from exc
    if net.version != 4:
        raise ValidationError("Only IPv4 subnets are supported.")
    return net  # type: ignore[return-value]


def validate_scan_scope(network: ipaddress.IPv4Network,
                        interface_network: ipaddress.IPv4Network,
                        max_hosts: int) -> None:
    """Refuse any scope that is not a private subnet inside the chosen interface's network."""
    if not network.is_private:
        raise ValidationError(f"{network} is not a private (RFC 1918 / link-local) range; refusing to scan.")
    if network.is_loopback or network.is_multicast or network.is_unspecified:
        raise ValidationError(f"{network} is not a valid scan target.")
    if not network.subnet_of(interface_network):
        raise ValidationError(
            f"{network} is outside the selected interface's network {interface_network}. "
            "Scanning is restricted to the network the interface is attached to.")
    hosts = max(network.num_addresses - 2, 1)
    if hosts > max_hosts:
        raise ValidationError(f"{network} has {hosts} hosts; the configured limit is {max_hosts}.")


def validate_range(name: str, value: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number.") from exc
    if not lo <= number <= hi:
        raise ValidationError(f"{name} must be between {lo:g} and {hi:g}.")
    return number
