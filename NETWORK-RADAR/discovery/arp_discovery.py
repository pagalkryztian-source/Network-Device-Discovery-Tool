"""ARP host discovery with Scapy, restricted to the validated scan scope."""
from __future__ import annotations

import logging
import threading
from typing import Any, Iterable, Optional

from config.defaults import ARP_TIMEOUT_S
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from network.network_utils import has_raw_socket_privilege, ip_in_network
from network.subnet_manager import SubnetManager
from utils.validators import normalize_mac

log = logging.getLogger(__name__)


def parse_arp_responses(answered: Iterable[Any], network) -> list[Observation]:
    """Turn Scapy (sent, received) pairs into deduplicated observations.

    Responses claiming an IP outside `network`, or with an unusable MAC, are ignored.
    """
    seen: dict[str, Observation] = {}
    for pair in answered:
        recv = pair[1] if isinstance(pair, (tuple, list)) else pair
        ip = getattr(recv, "psrc", None)
        mac = normalize_mac(getattr(recv, "hwsrc", None))
        if not ip or not mac or not ip_in_network(ip, network):
            continue
        seen[ip] = Observation(source="arp", ip=ip, mac=mac)
    return list(seen.values())


def _chunks(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class ARPDiscovery(BaseDiscovery):
    name = "arp"

    def __init__(self, scapy_api: Optional[Any] = None, timeout: float = ARP_TIMEOUT_S) -> None:
        self._api = scapy_api            # injectable for tests: needs Ether, ARP, srp
        self._timeout = timeout

    def _scapy(self) -> Any:
        if self._api is not None:
            return self._api
        try:
            from scapy.all import ARP, Ether, srp, conf  # noqa: WPS433 (lazy on purpose)
        except ImportError as exc:
            raise DiscoveryError("Scapy is not installed. Run: pip install scapy", fatal=True) from exc

        class _Api:
            pass
        api = _Api()
        api.Ether, api.ARP, api.srp = Ether, ARP, srp
        conf.verb = 0
        self._api = api
        return api

    def is_available(self) -> tuple[bool, str]:
        if self._api is None:
            try:
                import scapy  # noqa: F401
            except ImportError:
                return False, "Scapy is not installed (pip install scapy)."
            if not has_raw_socket_privilege():
                return False, ("ARP needs raw-socket privileges. Grant CAP_NET_RAW to the venv's python "
                               "(see README) or run the discovery with sudo.")
        return True, ""

    def discover(self, ctx: DiscoveryContext, cancel: threading.Event) -> list[Observation]:
        api = self._scapy()
        hosts = list(SubnetManager.iter_hosts(ctx.network, exclude=[ctx.interface.ip]))
        inter = 1.0 / max(ctx.packet_rate, 1)
        found: dict[str, Observation] = {}
        for batch in _chunks(hosts, max(ctx.batch_size, 1)):
            if cancel.is_set():
                break
            packets = [api.Ether(dst="ff:ff:ff:ff:ff:ff") / api.ARP(pdst=ip) for ip in batch]
            try:
                answered, _ = api.srp(packets, iface=ctx.interface.name, timeout=self._timeout,
                                      inter=inter, verbose=0, retry=0)
            except PermissionError as exc:
                raise DiscoveryError("Permission denied sending ARP. Grant CAP_NET_RAW or run with sudo.",
                                     fatal=True) from exc
            except OSError as exc:
                if exc.errno in (1, 13):
                    raise DiscoveryError("Permission denied sending ARP. Grant CAP_NET_RAW or run with sudo.",
                                         fatal=True) from exc
                raise DiscoveryError(f"Interface {ctx.interface.name} unavailable: {exc}", fatal=True) from exc
            for o in parse_arp_responses(answered, ctx.network):
                found[o.ip] = o
        # The scanning host never answers its own ARP request; report it from interface data.
        if not cancel.is_set():
            me = normalize_mac(ctx.interface.mac)
            found[ctx.interface.ip] = Observation(source="arp", ip=ctx.interface.ip, mac=me, is_self=True)
            if ctx.gateway and ctx.gateway in found:
                found[ctx.gateway].is_gateway = True
        log.info("ARP: %d host(s) answered on %s", len(found), ctx.network)
        return list(found.values())
