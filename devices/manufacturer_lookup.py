"""MAC -> manufacturer using locally installed OUI files (no network access)."""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Iterable, Optional

from network.network_utils import is_locally_administered
from utils.validators import normalize_mac

log = logging.getLogger(__name__)

DEFAULT_OUI_PATHS = (
    "/usr/share/ieee-data/oui.txt",         # Kali/Debian package: ieee-data
    "/var/lib/ieee-data/oui.txt",
    "/usr/share/arp-scan/ieee-oui.txt",
    "/usr/share/nmap/nmap-mac-prefixes",
    "/usr/share/wireshark/manuf",
)

# Tiny fallback so virtual NICs / Raspberry Pi resolve even without ieee-data.
BUILTIN = {
    "b827eb": "Raspberry Pi Foundation",
    "dca632": "Raspberry Pi Trading Ltd",
    "e45f01": "Raspberry Pi Trading Ltd",
    "000c29": "VMware, Inc.",
    "005056": "VMware, Inc.",
    "080027": "PCS Systemtechnik (VirtualBox)",
    "525400": "QEMU/KVM virtual NIC",
}

_IEEE = re.compile(r"^\s*([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+?)\s*$")
_SIMPLE = re.compile(r"^([0-9A-Fa-f]{6})\s+(.+?)\s*$")
_MANUF = re.compile(r"^([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\s+(.+?)\s*$")


class ManufacturerLookup:
    def __init__(self, paths: Iterable[str] = DEFAULT_OUI_PATHS) -> None:
        self._paths = list(paths)
        self._table: Optional[dict[str, str]] = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        table = dict(BUILTIN)
        for p in self._paths:
            path = Path(p)
            if not path.is_file():
                continue
            try:
                count = self._parse(path, table)
                log.info("Loaded %d OUI entries from %s", count, path)
                break                      # first readable database wins
            except OSError as exc:
                log.warning("Cannot read OUI file %s: %s", path, exc)
        else:
            log.warning("No OUI database found (install the 'ieee-data' package); using minimal built-in list.")
        return table

    @staticmethod
    def _parse(path: Path, table: dict[str, str]) -> int:
        n = 0
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#") or "(base 16)" in line:
                    continue
                m = _IEEE.match(line)
                if m:
                    table[(m.group(1) + m.group(2) + m.group(3)).lower()] = m.group(4)
                    n += 1
                    continue
                m = _MANUF.match(line)
                if m and "/" not in line.split("\t")[0]:
                    table[(m.group(1) + m.group(2) + m.group(3)).lower()] = m.group(4).split("\t")[-1]
                    n += 1
                    continue
                m = _SIMPLE.match(line)
                if m:
                    table[m.group(1).lower()] = m.group(2)
                    n += 1
        return n

    def lookup(self, mac: Optional[str]) -> Optional[str]:
        norm = normalize_mac(mac)
        if not norm or is_locally_administered(norm):
            return None                     # randomised MACs carry no vendor information
        with self._lock:
            if self._table is None:
                self._table = self._load()
        return self._table.get(norm.replace(":", "")[:6])
