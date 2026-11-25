"""Runtime-adjustable settings (in memory only)."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from config import defaults as d
from utils.validators import ValidationError, validate_range

DISCOVERY_FLAGS = {
    "arp": "enable_arp",
    "mdns": "enable_mdns",
    "ssdp": "enable_ssdp",
    "bluetooth": "enable_bluetooth",
    "wifi": "enable_wifi",
    "hostname": "enable_hostname",
    "probe": "enable_probe",
}


@dataclass
class Settings:
    scan_interval: float = d.DEFAULT_SCAN_INTERVAL_S
    missed_threshold: int = d.DEFAULT_MISSED_THRESHOLD
    packet_rate: int = d.DEFAULT_PACKET_RATE
    batch_size: int = d.DEFAULT_BATCH_SIZE
    concurrency: int = d.DEFAULT_CONCURRENCY
    probe_rate: int = d.DEFAULT_PROBE_RATE
    max_subnet_hosts: int = d.MAX_SUBNET_HOSTS
    enable_arp: bool = True
    enable_mdns: bool = True
    enable_ssdp: bool = True
    enable_hostname: bool = True
    enable_probe: bool = True
    enable_bluetooth: bool = True    # passive; skipped with a notice if no adapter/BlueZ
    enable_wifi: bool = True         # passive Wi-Fi radio survey (no connection needed)
    wifi_ref_rssi: float = d.DEFAULT_WIFI_REF_RSSI      # dBm at 1 m, calibrate for better distance
    bt_ref_rssi: float = d.DEFAULT_BT_REF_RSSI
    path_loss_exp: float = d.DEFAULT_PATH_LOSS_EXP

    def is_enabled(self, name: str) -> bool:
        flag = DISCOVERY_FLAGS.get(name)
        return bool(flag and getattr(self, flag))

    def validate(self) -> None:
        validate_range("Scan interval", self.scan_interval, d.MIN_SCAN_INTERVAL_S, d.MAX_SCAN_INTERVAL_S)
        validate_range("Missed observations", self.missed_threshold, 1, 100)
        validate_range("Packet rate", self.packet_rate, 1, d.MAX_PACKET_RATE)
        validate_range("Batch size", self.batch_size, 1, 512)
        validate_range("Concurrency", self.concurrency, 1, d.MAX_CONCURRENCY)
        validate_range("Probe rate", self.probe_rate, 1, 2000)
        validate_range("Wi-Fi reference RSSI", self.wifi_ref_rssi, -80, -10)
        validate_range("Bluetooth reference RSSI", self.bt_ref_rssi, -100, -20)
        validate_range("Path-loss exponent", self.path_loss_exp, 1.5, 5.0)

    def update(self, **values: Any) -> None:
        """Apply values atomically: on invalid input nothing changes."""
        names = {f.name for f in fields(self)}
        unknown = set(values) - names
        if unknown:
            raise ValidationError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        old = {n: getattr(self, n) for n in names}
        for k, v in values.items():
            setattr(self, k, v)
        try:
            self.validate()
        except ValidationError:
            for k, v in old.items():
                setattr(self, k, v)
            raise
