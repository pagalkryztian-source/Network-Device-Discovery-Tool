"""SSDP/UPnP discovery (M-SEARCH) plus a bounded fetch of device descriptions."""
from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config.defaults import SSDP_FETCH_TIMEOUT_S, SSDP_LISTEN_S, SSDP_MAX_DESCRIPTION_BYTES
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from network.network_utils import ip_in_network

log = logging.getLogger(__name__)
SSDP_ADDR = ("239.255.255.250", 1900)
M_SEARCH = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
            "MX: 2\r\nST: ssdp:all\r\nUSER-AGENT: NetworkRadar/1.0\r\n\r\n").encode()


def parse_ssdp_response(data: bytes) -> dict[str, str]:
    """Parse the header block of an SSDP response/NOTIFY into a lowercase-keyed dict."""
    text = data.decode("utf-8", "replace")
    lines = text.split("\r\n")
    if not lines or not (lines[0].startswith("HTTP/1.1 200") or lines[0].startswith("NOTIFY")):
        return {}
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def parse_description(xml_bytes: bytes) -> dict[str, str]:
    """Extract a few descriptive fields from a UPnP description document (XXE-safe)."""
    lowered = xml_bytes[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        return {}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}
    wanted = {"friendlyname": "friendlyName", "manufacturer": "manufacturer",
              "modelname": "modelName", "devicetype": "deviceType"}
    out: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in wanted and el.text and wanted[tag] not in out:
            out[wanted[tag]] = el.text.strip()[:120]
    return out


class SSDPDiscovery(BaseDiscovery):
    name = "ssdp"

    def __init__(self, listen_s: float = SSDP_LISTEN_S, fetch_descriptions: bool = True) -> None:
        self._listen_s = listen_s
        self._fetch = fetch_descriptions

    def _fetch_description(self, url: str, ip: str, ctx: DiscoveryContext) -> dict[str, str]:
        parsed = urllib.parse.urlparse(url)
        # Only fetch from the very host that answered, and only inside the authorised scope.
        if parsed.scheme != "http" or parsed.hostname != ip or not ip_in_network(ip, ctx.network):
            return {}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NetworkRadar/1.0"})
            with urllib.request.urlopen(req, timeout=SSDP_FETCH_TIMEOUT_S) as resp:  # nosec B310 (http only, checked)
                return parse_description(resp.read(SSDP_MAX_DESCRIPTION_BYTES))
        except Exception as exc:
            log.debug("SSDP description fetch failed for %s: %s", url, exc)
            return {}

    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ctx.interface.ip))
            sock.bind((ctx.interface.ip, 0))
        except OSError as exc:
            raise DiscoveryError(f"Cannot open SSDP socket on {ctx.interface.name}: {exc}") from exc
        found: dict[str, dict[str, str]] = {}
        locations: dict[str, str] = {}
        try:
            sock.sendto(M_SEARCH, SSDP_ADDR)
            deadline = time.monotonic() + self._listen_s
            while time.monotonic() < deadline and not cancel.is_set():
                sock.settimeout(max(0.05, min(0.25, deadline - time.monotonic())))
                try:
                    data, (src, _p) = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if not ip_in_network(src, ctx.network):
                    continue
                h = parse_ssdp_response(data)
                if not h:
                    continue
                info = found.setdefault(src, {})
                for key, field in (("server", "server"), ("usn", "usn")):
                    if h.get(key) and field not in info:
                        info[field] = h[key][:200]
                st = {s for s in info.get("st", "").split(", ") if s} | {h.get("st") or h.get("nt") or ""}
                info["st"] = ", ".join(sorted(s for s in st if s))[:400]
                if h.get("location"):
                    locations.setdefault(src, h["location"])
        except OSError as exc:
            raise DiscoveryError(f"SSDP failure: {exc}") from exc
        finally:
            sock.close()

        if self._fetch and locations and not cancel.is_set():
            with ThreadPoolExecutor(max_workers=min(ctx.concurrency, 8)) as pool:
                futures = {ip: pool.submit(self._fetch_description, url, ip, ctx) for ip, url in locations.items()}
                for ip, fut in futures.items():
                    found[ip].update(fut.result())
        return [Observation(source="ssdp", ip=ip, ssdp=info) for ip, info in found.items()]
