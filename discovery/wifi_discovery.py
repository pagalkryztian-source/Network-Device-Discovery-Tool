"""Passive Wi-Fi survey: lists every access point whose beacon the Wi-Fi radio can hear.

* Needs a Wi-Fi adapter that is switched on, but NOT a connection and NOT internet.
* Reads the kernel scan cache with `iw dev <iface> scan dump` (no root needed) and asks
  NetworkManager to refresh it first (`nmcli device wifi rescan`, best effort trigger).
* It only listens to broadcast beacons. It never joins, probes or attacks any network, so it
  can see networks outside your own - that is how every Wi-Fi analyser works.
* Gives signal strength (RSSI) per access point; distance is estimated from it, direction is not
  measurable with one antenna.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from typing import Optional

from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from utils.signal import decode_ssid, wifi_band, wifi_channel
from utils.validators import normalize_mac

log = logging.getLogger(__name__)
MAX_AGE_MS = 30_000
_BSS = re.compile(r"^BSS\s+((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})")
_SIGNAL = re.compile(r"^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm")
_FREQ = re.compile(r"^\s*freq:\s*(\d+)")
_SEEN = re.compile(r"^\s*last seen:\s*(\d+)\s*ms ago")
_SSID = re.compile(r"^\s*SSID:\s?(.*)$")
_IFACE = re.compile(r"^\s*Interface\s+(\S+)")


def parse_iw_interfaces(output: str) -> list[str]:
    return [m.group(1) for line in output.splitlines() if (m := _IFACE.match(line))]


def parse_iw_scan(output: str, max_age_ms: int = MAX_AGE_MS) -> list[Observation]:
    """Parse `iw dev X scan dump`. Entries not heard for max_age_ms are dropped."""
    blocks: list[tuple[str, list[str]]] = []
    for line in output.splitlines():
        m = _BSS.match(line)
        if m:
            blocks.append((m.group(1), []))
        elif blocks:
            blocks[-1][1].append(line)
    result: dict[str, Observation] = {}
    for bssid, lines in blocks:
        mac = normalize_mac(bssid)
        if not mac:
            continue
        rssi = freq = age = None
        ssid: Optional[str] = None
        for line in lines:
            if (m := _SIGNAL.match(line)):
                rssi = int(round(float(m.group(1))))
            elif (m := _FREQ.match(line)):
                freq = int(m.group(1))
            elif (m := _SEEN.match(line)):
                age = int(m.group(1))
            elif (m := _SSID.match(line)) and ssid is None:
                ssid = decode_ssid(m.group(1))
        if rssi is None or (age is not None and age > max_age_ms):
            continue
        text = "\n".join(lines)
        if "SAE" in text:
            security = "WPA3"
        elif "RSN:" in text:
            security = "WPA2"
        elif "WPA:" in text:
            security = "WPA"
        elif "Privacy" in text:
            security = "WEP"
        else:
            security = "Open"
        result[mac] = Observation(source="wifi", mac=mac, transport="wifi", rssi=rssi, ssid=ssid,
                                  channel=wifi_channel(freq), band=wifi_band(freq), security=security)
    return list(result.values())


class WiFiDiscovery(BaseDiscovery):
    name = "wifi"
    requires_network = False

    def __init__(self, runner=None) -> None:
        self._run = runner or self._subprocess_run          # injectable for tests

    @staticmethod
    def _subprocess_run(cmd: list[str], timeout: float) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiscoveryError(f"{cmd[0]} failed: {exc}") from exc
        if proc.returncode != 0:
            raise DiscoveryError(f"{' '.join(cmd[:4])} exited {proc.returncode}: {proc.stderr.strip()[:160]}")
        return proc.stdout

    def is_available(self) -> tuple[bool, str]:
        if shutil.which("iw") is None:
            return False, "'iw' not found (sudo apt install iw) - needed for the Wi-Fi radio survey."
        return True, ""

    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        ifaces = parse_iw_interfaces(self._run(["iw", "dev"], 5))
        if not ifaces:
            raise DiscoveryError("No Wi-Fi adapter found (check 'iw dev' and that Wi-Fi is not hard-blocked).")
        observations: dict[str, Observation] = {}
        for iface in ifaces:
            if cancel.is_set():
                break
            if shutil.which("nmcli"):                       # refresh trigger only; data comes from iw
                try:
                    self._run(["nmcli", "device", "wifi", "rescan", "ifname", iface], 10)
                except DiscoveryError as exc:
                    log.warning("Wi-Fi rescan trigger failed on %s (showing cached scan): %s", iface, exc)
            for obs in parse_iw_scan(self._run(["iw", "dev", iface, "scan", "dump"], 10)):
                best = observations.get(obs.mac)
                if best is None or (obs.rssi or -200) > (best.rssi or -200):
                    observations[obs.mac] = obs
        log.info("Wi-Fi: %d access point(s) heard", len(observations))
        return list(observations.values())
