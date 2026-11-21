"""ApplicationController: wires components together (dependency injection) for the GUI."""
from __future__ import annotations

import logging
from typing import Optional

from config.settings import Settings
from core.event_manager import Event, EventBus
from core.network_monitor import NetworkMonitor
from devices.device import Category, Device
from devices.device_classifier import DeviceClassifier
from devices.device_registry import DeviceRegistry
from devices.manufacturer_lookup import ManufacturerLookup
from discovery.arp_discovery import ARPDiscovery
from discovery.base_discovery import BaseDiscovery
from discovery.bluetooth_discovery import BluetoothDiscovery
from discovery.mdns_discovery import MDNSDiscovery
from discovery.mesh_relay import MeshMessage, MeshMessageType, MeshRelay
from discovery.ssdp_discovery import SSDPDiscovery
from discovery.wifi_discovery import WiFiDiscovery
from network.interface_manager import InterfaceInfo, NetworkInterfaceManager
from network.network_utils import get_default_gateway, has_raw_socket_privilege
from network.subnet_manager import SubnetManager

log = logging.getLogger(__name__)


class ApplicationController:
    def __init__(self, settings: Optional[Settings] = None,
                 interfaces: Optional[NetworkInterfaceManager] = None,
                 discoverers: Optional[list[BaseDiscovery]] = None,
                 registry: Optional[DeviceRegistry] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.settings = settings or Settings()
        self.interfaces = interfaces or NetworkInterfaceManager()
        self.bus = bus or EventBus()
        self.registry = registry or DeviceRegistry(ManufacturerLookup(), DeviceClassifier(),
                                                   self.settings.missed_threshold)
        self.mesh = MeshRelay()           # BLE mesh-lite emergency relay (see discovery/mesh_relay.py)
        self.monitor = NetworkMonitor(
            self.registry, self.bus,
            discoverers or [ARPDiscovery(), MDNSDiscovery(), SSDPDiscovery(), WiFiDiscovery(),
                                                  BluetoothDiscovery()],
            self.settings, mesh=self.mesh)
        self.current_interface: Optional[InterfaceInfo] = None
        self.current_network = None

    def list_interfaces(self) -> list[InterfaceInfo]:
        return self.interfaces.list_interfaces()

    @staticmethod
    def raw_privileges_ok() -> bool:
        return has_raw_socket_privilege()

    def apply_settings(self, **values) -> None:
        self.settings.update(**values)

    def start_scan(self, interface_name: str, subnet_text: Optional[str], authorized: bool) -> None:
        """Validate scope and start monitoring. `authorized` must be an explicit user confirmation."""
        if not authorized:
            raise PermissionError("Scanning requires explicit confirmation that you are authorised to scan this network.")
        if self.monitor.is_running:
            raise RuntimeError("Scanning is already running.")
        self.settings.validate()
        iface = self.interfaces.get(interface_name)
        network = SubnetManager.resolve_scope(iface, subnet_text, self.settings.max_subnet_hosts)
        self.current_interface, self.current_network = iface, network
        log.info("Starting scan on %s, scope %s", iface.name, network)
        self.monitor.start(iface, network, get_default_gateway(iface.name))

    def start_radio(self) -> None:
        """Passive radio-only mode: listens for Wi-Fi beacons and Bluetooth advertising.

        Needs no network, no Wi-Fi connection and no internet, and sends nothing to any host,
        so there is no scan scope to authorise.
        """
        if self.monitor.is_running:
            raise RuntimeError("Scanning is already running.")
        self.settings.validate()
        self.current_interface = self.current_network = None
        log.info("Starting passive radio-only mode")
        self.monitor.start(None, None, None)

    def stop_scan(self) -> None:
        self.monitor.stop()

    def poll_events(self, max_items: int = 500) -> list[Event]:
        return self.bus.drain(max_items)

    def set_manual_category(self, key: str, category: Optional[Category]) -> Optional[Device]:
        return self.registry.set_manual_category(key, category)

    def set_tracked(self, key: str, tracked: bool) -> Optional[Device]:
        """Search mode: mark one device as a target so TARGET_ALERT events fire for it
        (very close / lost / reappeared). See devices/device_registry.py for the honest
        limits - this is an RSSI-distance trend, never a GPS position or bearing."""
        return self.registry.set_tracked(key, tracked)

    def send_mesh_sos(self, text: str = "") -> MeshMessage:
        """Broadcast an SOS over the BLE mesh-lite relay. Only reaches other devices running
        this software (or compatible listeners) within Bluetooth range, relayed hop-by-hop from
        there - see discovery/mesh_relay.py's module docstring for the honest limits."""
        return self.mesh.send(MeshMessageType.SOS, text=text)

    def send_mesh_status(self, text: str) -> MeshMessage:
        return self.mesh.send(MeshMessageType.STATUS, text=text)

    def shutdown(self) -> None:
        self.monitor.stop()
        self.registry.clear()          # RAM only: nothing survives the process
