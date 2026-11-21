"""Optional Bluetooth discovery through BlueZ's `bluetoothctl` (best effort).

Only devices that are discoverable/advertising during the scan window are seen.
Many phones/watches use randomised BLE addresses, so identities are unstable.
Bluetooth is not an IP network, so no subnet is needed. RSSI is captured for distance estimates.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time

from config.defaults import BLUETOOTH_SCAN_S
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from utils.signal import median
from utils.validators import normalize_mac

log = logging.getLogger(__name__)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
_LINE = re.compile(r"\[(?:NEW|CHG)\]\s+Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s*(?:Name:\s*|Alias:\s*)?(.*)$")


def parse_bluetoothctl(output: str) -> dict[str, str]:
    devices: dict[str, str] = {}
    for raw in output.splitlines():
        m = _LINE.search(_ANSI.sub("", raw).strip())
        if not m:
            continue
        mac = normalize_mac(m.group(1))
        if not mac:
            continue
        name = m.group(2).strip()
        if name and not name.startswith(("RSSI:", "ManufacturerData", "ServiceData", "UUIDs", "TxPower", "Paired", "Connected")):
            if name.replace("-", ":").lower() != mac:        # BlueZ echoes the MAC when unnamed
                devices[mac] = name
                continue
        devices.setdefault(mac, "")
    return devices


_RSSI = re.compile(r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+RSSI:\s*(?:0x[0-9a-fA-F]+\s*\()?(-?\d+)\)?")


def parse_bluetoothctl_rssi(output: str) -> dict[str, int]:
    """Median RSSI (dBm) per device over all readings in the scan window."""
    samples: dict[str, list[int]] = {}
    for raw in output.splitlines():
        m = _RSSI.search(_ANSI.sub("", raw))
        if not m:
            continue
        mac = normalize_mac(m.group(1))
        value = int(m.group(2))
        if mac and -127 <= value <= 20:
            samples.setdefault(mac, []).append(value)
    return {mac: int(round(median(v))) for mac, v in samples.items()}


class BluetoothDiscovery(BaseDiscovery):
    name = "bluetooth"
    requires_network = False

    def __init__(self, scan_s: int = BLUETOOTH_SCAN_S) -> None:
        self._scan_s = scan_s

    def is_available(self) -> tuple[bool, str]:
        if shutil.which("bluetoothctl") is None:
            return False, "bluetoothctl not found (sudo apt install bluez) or no Bluetooth adapter."
        return True, ""

    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        try:
            proc = subprocess.Popen(["bluetoothctl", "--timeout", str(self._scan_s), "scan", "on"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            raise DiscoveryError(f"Cannot start bluetoothctl: {exc}") from exc
        deadline = time.monotonic() + self._scan_s + 5
        try:
            while proc.poll() is None and time.monotonic() < deadline and not cancel.is_set():
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                out, _ = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
        text = out or ""
        rssi = parse_bluetoothctl_rssi(text)
        return [Observation(source="bluetooth", mac=mac, bt_name=name or None, transport="bluetooth",
                            rssi=rssi.get(mac))
                for mac, name in parse_bluetoothctl(text).items()]
