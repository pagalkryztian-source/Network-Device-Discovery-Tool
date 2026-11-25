"""Continuous monitoring: runs discovery cycles in a background thread and emits events."""
from __future__ import annotations

import copy
import ipaddress
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Sequence

from config import defaults as d
from config.settings import Settings
from core.event_manager import EventBus, EventType
from devices.device_registry import ApplyResult, ChangeKind, DeviceRegistry
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from network.interface_manager import InterfaceInfo
from network.network_utils import ip_in_network, probe_ports, resolve_hostname
from utils.rate_limiter import RateLimiter
from utils.signal import PathLossModel

log = logging.getLogger(__name__)


class NetworkMonitor:
    """Owns the scan loop. Never touches GUI code; communicates only through the EventBus."""

    def __init__(self, registry: DeviceRegistry, bus: EventBus, discoverers: Sequence[BaseDiscovery],
                 settings: Settings,
                 hostname_resolver: Callable[[str], Optional[str]] = resolve_hostname,
                 port_prober: Callable[..., list[int]] = probe_ports) -> None:
        self.registry, self.bus, self.settings = registry, bus, settings
        self._discoverers = list(discoverers)
        self._resolve, self._probe = hostname_resolver, port_prober
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()           # prevents overlapping scans
        self._thread: Optional[threading.Thread] = None
        self._ctx: Optional[DiscoveryContext] = None
        self._fatal = False

    # ------------------------------------------------------------------ lifecycle
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, interface: Optional[InterfaceInfo], network: Optional[ipaddress.IPv4Network],
              gateway: Optional[str] = None) -> None:
        """interface/network = None starts radio-only mode (Wi-Fi beacons + Bluetooth, no network needed)."""
        if self.is_running:
            raise RuntimeError("Monitor already running")
        cfg = self.settings
        self._ctx = DiscoveryContext(interface, network, cfg.packet_rate, cfg.batch_size, cfg.concurrency, gateway)
        self._stop.clear()
        self._fatal = False
        self._thread = threading.Thread(target=self._run, name="network-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)
            if t.is_alive():
                log.warning("Monitor thread did not stop within %.0fs", timeout)

    def _run(self) -> None:
        assert self._ctx is not None
        radio_only = self._ctx.network is None
        self.bus.publish(EventType.SCAN_STARTED, interface="radio" if radio_only else self._ctx.interface.name,
                         network="radio only (no network needed)" if radio_only else str(self._ctx.network))
        try:
            while not self._stop.is_set():
                self.run_cycle()
                if self._fatal:
                    break
                self._stop.wait(max(self.settings.scan_interval, d.MIN_SCAN_INTERVAL_S))
        except Exception:
            log.exception("Monitor loop crashed")
            self.bus.publish(EventType.SCAN_ERROR, message="Internal monitor error; see log.", fatal=True)
        finally:
            self.bus.publish(EventType.SCAN_STOPPED)

    # ------------------------------------------------------------------ one cycle
    def run_cycle(self) -> bool:
        """Run one discovery cycle. Returns False if skipped because a cycle is already running."""
        if self._ctx is None:
            raise RuntimeError("Monitor has no scan context; call start() first")
        if not self._cycle_lock.acquire(blocking=False):
            log.warning("Previous scan still running; skipping this cycle")
            return False
        try:
            self._cycle(copy.copy(self.settings), self._ctx)
            return True
        finally:
            self._cycle_lock.release()

    def _cycle(self, cfg: Settings, ctx: DiscoveryContext) -> None:
        started = time.time()
        self.registry.missed_threshold = cfg.missed_threshold
        self.registry.set_signal_models(PathLossModel(cfg.wifi_ref_rssi, cfg.path_loss_exp),
                                        PathLossModel(cfg.bt_ref_rssi, cfg.path_loss_exp))
        self.registry.begin_cycle()
        self.bus.publish(EventType.CYCLE_STARTED)
        active: set[str] = set()

        runnable = []
        for disc in self._discoverers:
            if not cfg.is_enabled(disc.name):
                continue
            if disc.requires_network and ctx.network is None:
                continue                                    # radio-only mode: no IP network to scan
            ok, why = disc.is_available()
            if not ok:
                self.bus.publish(EventType.SCAN_ERROR, message=f"{disc.name}: {why}", source=disc.name,
                                 fatal=disc.name == "arp")
                if disc.name == "arp":
                    self._fatal = True
                    return
                continue
            runnable.append(disc)

        if runnable:
            with ThreadPoolExecutor(max_workers=len(runnable), thread_name_prefix="discovery") as pool:
                futures = {pool.submit(dsc.discover, ctx, self._stop): dsc for dsc in runnable}
                for fut in as_completed(futures):
                    dsc = futures[fut]
                    try:
                        observations = fut.result()
                    except DiscoveryError as exc:
                        log.error("%s discovery failed: %s", dsc.name, exc)
                        self.bus.publish(EventType.SCAN_ERROR, message=f"{dsc.name}: {exc}",
                                         source=dsc.name, fatal=exc.fatal)
                        if exc.fatal:
                            self._fatal = True
                        continue
                    except Exception as exc:
                        log.exception("%s discovery crashed", dsc.name)
                        self.bus.publish(EventType.SCAN_ERROR, message=f"{dsc.name}: unexpected error: {exc}",
                                         source=dsc.name, fatal=False)
                        continue
                    self._apply(observations, ctx)
                    if not self._stop.is_set():
                        active.add(dsc.name)

        if self._stop.is_set() or self._fatal:
            return
        if ctx.network is not None:
            self._enrich(cfg, ctx)
        if self._stop.is_set():
            return
        for dev in self.registry.end_cycle(active):
            self.bus.publish(EventType.DEVICE_OFFLINE, device=dev)
            if dev.tracked:
                self.bus.publish(EventType.TARGET_ALERT, device=dev, reason="lost")
        self.bus.publish(EventType.CYCLE_COMPLETED, duration=time.time() - started,
                         online=self.registry.online_count, total=self.registry.total_count)

    # ------------------------------------------------------------------ helpers
    def _apply(self, observations: Sequence[Observation], ctx: DiscoveryContext) -> None:
        for obs in observations:
            if obs.transport == "ip" and obs.ip and not ip_in_network(obs.ip, ctx.network):
                log.debug("Ignoring out-of-scope observation %s from %s", obs.ip, obs.source)
                continue
            try:
                self._publish(self.registry.apply(obs))
            except ValueError as exc:
                log.debug("Dropping invalid observation from %s: %s", obs.source, exc)

    def _publish(self, res: ApplyResult) -> None:
        mapping = {ChangeKind.NEW: EventType.DEVICE_DISCOVERED, ChangeKind.REAPPEARED: EventType.DEVICE_REAPPEARED,
                   ChangeKind.UPDATED: EventType.DEVICE_UPDATED}
        etype = mapping.get(res.kind)
        if etype:
            self.bus.publish(etype, device=res.device)
        if res.classification_changed and res.kind is not ChangeKind.NEW:
            self.bus.publish(EventType.CLASSIFICATION_UPDATED, device=res.device)
        if res.target_alert is not None:
            self.bus.publish(EventType.TARGET_ALERT, device=res.device, reason=res.target_alert.name.lower())

    def _enrich(self, cfg: Settings, ctx: DiscoveryContext) -> None:
        want_h, want_p = cfg.enable_hostname, cfg.enable_probe
        if not (want_h or want_p):
            return
        targets = self.registry.enrichment_targets(d.HOSTNAME_RETRY_S, d.PROBE_REFRESH_S)
        targets = [t for t in targets if (t[2] and want_h) or (t[3] and want_p)]
        if not targets:
            return
        limiter = RateLimiter(cfg.probe_rate)

        def work(t: tuple[str, str, bool, bool]) -> list[Observation]:
            _key, ip, need_h, need_p = t
            out: list[Observation] = []
            if self._stop.is_set():
                return out
            if need_h and want_h:
                out.append(Observation(source="hostname", ip=ip, hostname=self._resolve(ip), confirms_presence=False))
            if need_p and want_p and not self._stop.is_set():
                ports = self._probe(ip, d.PROBE_PORTS, d.PROBE_TIMEOUT_S, limiter, self._stop)
                out.append(Observation(source="probe", ip=ip, open_ports=set(ports), confirms_presence=False))
            return out

        with ThreadPoolExecutor(max_workers=cfg.concurrency, thread_name_prefix="enrich") as pool:
            for fut in as_completed([pool.submit(work, t) for t in targets]):
                try:
                    self._apply(fut.result(), ctx)
                except Exception:
                    log.exception("Enrichment task failed")
