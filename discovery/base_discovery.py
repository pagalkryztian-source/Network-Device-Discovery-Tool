from __future__ import annotations

import ipaddress
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from devices.observation import Observation
from network.interface_manager import InterfaceInfo


class DiscoveryError(RuntimeError):
    """A discovery module could not run. fatal=True means scanning cannot continue at all."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class DiscoveryContext:
    interface: Optional[InterfaceInfo] = None      # None = radio-only mode (no network needed)
    network: Optional[ipaddress.IPv4Network] = None
    packet_rate: int = 100
    batch_size: int = 64
    concurrency: int = 16
    gateway: Optional[str] = None


class BaseDiscovery(ABC):
    """Interface implemented by every discovery method (polymorphic use by NetworkMonitor)."""

    name: str = "base"
    requires_network: bool = True      # False for passive radio sources (Wi-Fi beacons, Bluetooth)

    def is_available(self) -> tuple[bool, str]:
        return True, ""

    @abstractmethod
    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        """Return observations. Must honour `cancel` promptly and raise DiscoveryError on failure."""
