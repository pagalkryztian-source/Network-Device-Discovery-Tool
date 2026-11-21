"""Minimal mDNS (multicast DNS) service discovery using only the standard library.

Sends legacy-unicast style queries (source port != 5353, QU bit set) so replies come
straight back to our socket; no multicast group membership or avahi is required.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
import threading
import time
from typing import Optional

from config.defaults import MDNS_LISTEN_S
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from network.network_utils import ip_in_network

log = logging.getLogger(__name__)
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
META_QUERY = "_services._dns-sd._udp.local"
COMMON_SERVICES = [
    "_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp", "_scanner._tcp", "_airplay._tcp",
    "_googlecast._tcp", "_androidtvremote2._tcp", "_mediaremotetv._tcp", "_hap._tcp", "_homekit._tcp",
    "_companion-link._tcp", "_apple-mobdev2._tcp", "_rtsp._tcp", "_onvif._tcp", "_axis-video._tcp",
    "_smb._tcp", "_afpovertcp._tcp", "_adisk._tcp", "_workstation._tcp", "_ssh._tcp", "_esphomelib._tcp",
    "_hue._tcp", "_matter._tcp", "_sonos._tcp", "_spotify-connect._tcp", "_http._tcp",
]
_TYPE_RE = re.compile(r"(_[a-z0-9-]+\._(?:tcp|udp))\.local\.?$", re.I)
T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad DNS label: {label!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(names: list[str]) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, len(names), 0, 0, 0)
    body = b"".join(encode_name(n + ".local" if not n.endswith(".local") else n)
                    + struct.pack("!HH", T_PTR, 0x8001) for n in names)   # QU bit + class IN
    return header + body


def _read_name(data: bytes, off: int) -> tuple[str, int]:
    labels: list[str] = []
    end: Optional[int] = None
    hops = 0
    while True:
        if off >= len(data):
            raise ValueError("truncated name")
        length = data[off]
        if length == 0:
            off += 1
            break
        if length & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise ValueError("truncated pointer")
            if end is None:
                end = off + 2
            off = ((length & 0x3F) << 8) | data[off + 1]
            hops += 1
            if hops > 30:
                raise ValueError("compression loop")
            continue
        off += 1
        if off + length > len(data):
            raise ValueError("truncated label")
        labels.append(data[off:off + length].decode("utf-8", "replace"))
        off += length
    return ".".join(labels), (end if end is not None else off)


def parse_message(data: bytes) -> list[tuple[str, int, object]]:
    """Return [(owner, rtype, parsed_rdata)] for A/PTR/SRV records; malformed input -> []."""
    try:
        if len(data) < 12:
            return []
        _id, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        off = 12
        for _ in range(qd):
            _, off = _read_name(data, off)
            off += 4
        records = []
        for _ in range(an + ns + ar):
            owner, off = _read_name(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
            off += 10
            rdata_off = off
            off += rdlen
            if off > len(data):
                break
            if rtype == T_PTR:
                records.append((owner, rtype, _read_name(data, rdata_off)[0]))
            elif rtype == T_SRV and rdlen >= 7:
                records.append((owner, rtype, _read_name(data, rdata_off + 6)[0]))
            elif rtype == T_A and rdlen == 4:
                records.append((owner, rtype, socket.inet_ntoa(data[rdata_off:rdata_off + 4])))
        return records
    except (ValueError, struct.error):
        return []


def extract_info(records: list[tuple[str, int, object]]) -> tuple[Optional[str], set[str], set[str]]:
    """-> (hostname, service types of this device, service types merely listed by the meta-query)."""
    host: Optional[str] = None
    services: set[str] = set()
    listed: set[str] = set()
    for owner, rtype, rdata in records:
        o = owner.lower()
        if rtype == T_PTR:
            if o == META_QUERY:
                m = _TYPE_RE.search(str(rdata))
                if m:
                    listed.add(m.group(1).lower())
                    services.add(m.group(1).lower())
            else:
                m = _TYPE_RE.search(o)
                if m:
                    services.add(m.group(1).lower())
        elif rtype == T_SRV:
            m = _TYPE_RE.search(o)
            if m:
                services.add(m.group(1).lower())
            host = host or str(rdata)
        elif rtype == T_A:
            host = host or owner
    if host:
        host = re.sub(r"\.local\.?$", "", host, flags=re.I) or None
    return host, services, listed


class MDNSDiscovery(BaseDiscovery):
    name = "mdns"

    def __init__(self, listen_s: float = MDNS_LISTEN_S) -> None:
        self._listen_s = listen_s

    def _collect(self, sock: socket.socket, deadline: float, ctx: DiscoveryContext,
                 cancel: threading.Event, acc: dict[str, dict], types: set[str]) -> None:
        while time.monotonic() < deadline and not cancel.is_set():
            sock.settimeout(max(0.05, min(0.25, deadline - time.monotonic())))
            try:
                data, (src, _port) = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError as exc:
                raise DiscoveryError(f"mDNS socket error: {exc}") from exc
            if not ip_in_network(src, ctx.network):
                continue
            host, services, listed = extract_info(parse_message(data))
            types |= listed
            entry = acc.setdefault(src, {"host": None, "services": set()})
            entry["host"] = entry["host"] or host
            entry["services"] |= services

    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ctx.interface.ip))
            sock.bind((ctx.interface.ip, 0))
        except OSError as exc:
            raise DiscoveryError(f"Cannot open mDNS socket on {ctx.interface.name}: {exc}") from exc
        acc: dict[str, dict] = {}
        types: set[str] = set()
        try:
            sock.sendto(build_query([META_QUERY] + COMMON_SERVICES), (MDNS_GROUP, MDNS_PORT))
            self._collect(sock, time.monotonic() + self._listen_s, ctx, cancel, acc, types)
            extra = sorted(t for t in types if t not in COMMON_SERVICES)[:20]
            if extra and not cancel.is_set():
                sock.sendto(build_query(extra), (MDNS_GROUP, MDNS_PORT))
                self._collect(sock, time.monotonic() + self._listen_s / 2, ctx, cancel, acc, types)
        except OSError as exc:
            raise DiscoveryError(f"mDNS send failed: {exc}") from exc
        finally:
            sock.close()
        return [Observation(source="mdns", ip=ip, hostname=v["host"], mdns_services=v["services"])
                for ip, v in acc.items()]
