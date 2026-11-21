"""In-memory device store (no database, no persistence).

Complexity (n = devices, k = devices in a result):
  apply()                     O(1) expected  (hash lookups; classification is O(rules))
  begin_cycle()               O(1)
  end_cycle()                 O(o)           o = currently online devices
  counts()                    O(C)           C = number of categories (10), set intersections of
                                             size <= min(|category|, |online|)
  query(categories, text)     O(k) with category filter, O(n) for text search; + O(k) snapshots
  get() / set_manual_category O(1)
  Space                       O(n) devices + 3 O(n) indexes (mac, ip, category)
All public methods are guarded by one RLock and return *snapshots* (copies), never live objects.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Optional

from devices.device import Category, Device, Status
from devices.device_classifier import DeviceClassifier
from devices.manufacturer_lookup import ManufacturerLookup
from devices.observation import Observation
from network.network_utils import is_locally_administered
from utils.signal import DistanceTrend, KalmanRssi, PathLossModel, rssi_to_distance
from utils.validators import normalize_mac
from config.defaults import TARGET_CLOSE_THRESHOLD_M, TREND_DEADBAND_M, TREND_EMA_ALPHA, TREND_MIN_SAMPLES


class ChangeKind(Enum):
    NEW = auto()
    REAPPEARED = auto()
    UPDATED = auto()
    UNCHANGED = auto()


class TargetAlert(Enum):
    """Fired only for devices the user has explicitly marked as tracked (set_tracked)."""
    VERY_CLOSE = auto()       # smoothed distance crossed below TARGET_CLOSE_THRESHOLD_M
    LOST = auto()             # a tracked device just went offline
    REAPPEARED = auto()       # a tracked device came back


@dataclass(frozen=True)
class ApplyResult:
    device: Device
    kind: ChangeKind
    classification_changed: bool
    target_alert: Optional[TargetAlert] = None


class DeviceRegistry:
    def __init__(self, lookup: Optional[ManufacturerLookup] = None,
                 classifier: Optional[DeviceClassifier] = None,
                 missed_threshold: int = 3,
                 clock: Callable[[], float] = time.time) -> None:
        self._lookup = lookup or ManufacturerLookup()
        self._classifier = classifier or DeviceClassifier()
        self.missed_threshold = missed_threshold
        self._clock = clock
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {}
        self._by_mac: dict[str, str] = {}
        self._by_ip: dict[str, str] = {}
        self._online: set[str] = set()
        self._seen_cycle: set[str] = set()
        self._cat_index: dict[Category, set[str]] = {c: set() for c in Category}
        self._filters: dict[str, KalmanRssi] = {}
        self._trends: dict[str, DistanceTrend] = {}
        self._tracked: set[str] = set()
        self._models: dict[str, PathLossModel] = {"wifi": PathLossModel(-40.0, 2.7),
                                                  "bluetooth": PathLossModel(-59.0, 2.7)}

    # ---------------------------------------------------------------- internals
    def _index_add(self, dev: Device) -> None:
        self._devices[dev.key] = dev
        self._cat_index[dev.effective_category].add(dev.key)
        if dev.mac:
            self._by_mac[dev.mac] = dev.key
        if dev.ip:
            self._by_ip[dev.ip] = dev.key
        if dev.status is Status.ONLINE:
            self._online.add(dev.key)

    def _index_remove(self, dev: Device) -> None:
        self._devices.pop(dev.key, None)
        self._cat_index[dev.effective_category].discard(dev.key)
        self._online.discard(dev.key)
        self._seen_cycle.discard(dev.key)
        self._filters.pop(dev.key, None)
        self._trends.pop(dev.key, None)
        if dev.mac and self._by_mac.get(dev.mac) == dev.key:
            del self._by_mac[dev.mac]
        if dev.ip and self._by_ip.get(dev.ip) == dev.key:
            del self._by_ip[dev.ip]

    def _promote(self, dev: Device, mac: str) -> None:
        """An IP-keyed record learned its MAC: re-key it to the MAC (the stable identity)."""
        was_seen = dev.key in self._seen_cycle
        self._index_remove(dev)
        dev.key, dev.mac = mac, mac
        dev.randomized_mac = is_locally_administered(mac)
        dev.vendor = dev.vendor or self._lookup.lookup(mac)
        self._index_add(dev)
        if was_seen:
            self._seen_cycle.add(dev.key)

    def _merge(self, keep: Device, drop: Device) -> None:
        keep.hostname = keep.hostname or drop.hostname
        keep.mdns_services |= drop.mdns_services
        keep.open_ports |= drop.open_ports
        for k, v in drop.ssdp_info.items():
            keep.ssdp_info.setdefault(k, v)
        keep.sources |= drop.sources
        keep.first_seen = min(keep.first_seen, drop.first_seen) or keep.first_seen
        keep.last_probe = max(keep.last_probe, drop.last_probe)
        self._index_remove(drop)

    def _resolve(self, ip: Optional[str], mac: Optional[str]) -> Optional[Device]:
        by_mac = self._devices.get(self._by_mac.get(mac)) if mac else None      # type: ignore[arg-type]
        by_ip = self._devices.get(self._by_ip.get(ip)) if ip else None          # type: ignore[arg-type]
        if by_mac is not None:
            if by_ip is not None and by_ip is not by_mac:
                if by_ip.mac is None:
                    self._merge(by_mac, by_ip)              # IP-only record absorbed
                else:
                    del self._by_ip[ip]                     # IP moved from another device
            return by_mac
        if by_ip is not None:
            if mac is None:
                return by_ip
            if by_ip.mac is None:
                self._promote(by_ip, mac)
                return by_ip
            del self._by_ip[ip]                             # same IP, different MAC -> DHCP reassignment
        return None

    def set_signal_models(self, wifi: PathLossModel, bluetooth: PathLossModel) -> None:
        with self._lock:
            self._models = {"wifi": wifi, "bluetooth": bluetooth}

    def path_model(self, source: Optional[str]) -> Optional[PathLossModel]:
        with self._lock:
            return self._models.get(source) if source else None

    def _apply_signal(self, dev: Device, obs: Observation) -> bool:
        """Smooth RSSI with a per-device Kalman filter and refresh the distance estimate."""
        changed = False
        for attr in ("ssid", "channel", "band", "security"):
            value = getattr(obs, attr)
            if value is not None and getattr(dev, attr) != value:
                setattr(dev, attr, value)
                changed = True
        if obs.rssi is None or obs.source not in self._models:
            return changed
        filt = self._filters.setdefault(dev.key, KalmanRssi())
        smooth = filt.update(float(obs.rssi))
        distance = rssi_to_distance(smooth, self._models[obs.source])
        old = dev.distance_m
        if (old is None or abs(distance - old) / old > 0.05 or dev.rssi_source != obs.source
                or dev.rssi is None or abs(smooth - dev.rssi) >= 1.0):
            changed = True
        dev.rssi_raw, dev.rssi, dev.rssi_source, dev.distance_m = obs.rssi, smooth, obs.source, distance

        trend = self._trends.setdefault(dev.key, DistanceTrend(TREND_EMA_ALPHA, TREND_DEADBAND_M, TREND_MIN_SAMPLES))
        label = trend.update(distance)
        if label != dev.distance_trend:
            dev.distance_trend, changed = label, True
        return changed

    def _check_target_alert(self, dev: Device, was_offline_before: bool) -> Optional["TargetAlert"]:
        """Only ever evaluated for devices explicitly marked tracked via set_tracked()."""
        if dev.key not in self._tracked:
            return None
        if was_offline_before and dev.status is Status.ONLINE:
            return TargetAlert.REAPPEARED
        if dev.distance_m is not None and dev.distance_m <= TARGET_CLOSE_THRESHOLD_M:
            if not dev.was_close:
                dev.was_close = True
                return TargetAlert.VERY_CLOSE
        elif dev.distance_m is not None and dev.distance_m > TARGET_CLOSE_THRESHOLD_M * 1.3:
            dev.was_close = False          # re-arm the alert once it has clearly moved back away
        return None

    def _set_effective_index(self, dev: Device, old: Category) -> None:
        new = dev.effective_category
        if new is not old:
            self._cat_index[old].discard(dev.key)
            self._cat_index[new].add(dev.key)

    # ---------------------------------------------------------------- public API
    def apply(self, obs: Observation) -> ApplyResult:
        with self._lock:
            now = self._clock()
            mac = normalize_mac(obs.mac)
            if not (obs.ip or mac):
                raise ValueError("observation needs an IP or a MAC address")
            dev = self._resolve(obs.ip, mac)
            kind = ChangeKind.UNCHANGED
            changed = False
            was_offline_before = dev.status is Status.OFFLINE if dev is not None else False

            if dev is None:
                dev = Device(key=mac or f"ip:{obs.ip}", ip=obs.ip, mac=mac, transport=obs.transport,
                             first_seen=now, last_seen=now, status=Status.ONLINE)
                if mac:
                    dev.randomized_mac = is_locally_administered(mac)
                    dev.vendor = self._lookup.lookup(mac)
                self._index_add(dev)
                kind = ChangeKind.NEW
            else:
                if obs.ip and dev.ip != obs.ip:
                    if dev.ip and self._by_ip.get(dev.ip) == dev.key:
                        del self._by_ip[dev.ip]
                    dev.ip = obs.ip
                    changed = True
                if mac and dev.mac is None:
                    changed = True
                if dev.ip:
                    self._by_ip[dev.ip] = dev.key

            if obs.hostname and dev.hostname != obs.hostname:
                dev.hostname, changed = obs.hostname, True
            if obs.source == "hostname":
                dev.hostname_tried = now
            if obs.bt_name and dev.bt_name != obs.bt_name:
                dev.bt_name, changed = obs.bt_name, True
            if obs.mdns_services and not obs.mdns_services <= dev.mdns_services:
                dev.mdns_services |= obs.mdns_services
                changed = True
            for k, v in obs.ssdp.items():
                if v and dev.ssdp_info.get(k) != v:
                    dev.ssdp_info[k] = v
                    changed = True
            if obs.open_ports is not None:
                if dev.open_ports != obs.open_ports:
                    dev.open_ports = set(obs.open_ports)
                    changed = True
                dev.last_probe = now
            if self._apply_signal(dev, obs):
                changed = True
            if obs.is_self and not dev.is_self:
                dev.is_self, changed = True, True
            if obs.is_gateway and not dev.is_gateway:
                dev.is_gateway, changed = True, True

            if obs.confirms_presence:
                dev.sources.add(obs.source)
                dev.last_seen, dev.missed = now, 0
                self._seen_cycle.add(dev.key)
                if dev.status is Status.OFFLINE:
                    dev.status = Status.ONLINE
                    self._online.add(dev.key)
                    kind = ChangeKind.REAPPEARED

            cls_changed = self._reclassify(dev)
            if kind is ChangeKind.UNCHANGED and (changed or cls_changed):
                kind = ChangeKind.UPDATED
            alert = self._check_target_alert(dev, was_offline_before)
            return ApplyResult(dev.snapshot(), kind, cls_changed, alert)

    def _reclassify(self, dev: Device) -> bool:
        result = self._classifier.classify(dev)
        if (result.category, result.confidence, result.reasons) == (dev.category, dev.confidence, dev.reasons):
            return False
        old = dev.effective_category
        dev.category, dev.confidence, dev.reasons = result.category, result.confidence, result.reasons
        self._set_effective_index(dev, old)
        return True

    def begin_cycle(self) -> None:
        with self._lock:
            self._seen_cycle.clear()

    def end_cycle(self, active_sources: Iterable[str]) -> list[Device]:
        """Count a missed observation for online devices nobody reported this cycle.

        Only devices that were previously reported by a protocol that ran successfully
        this cycle are penalised, so a failed/disabled protocol never marks devices offline.
        """
        active = set(active_sources)
        gone: list[Device] = []
        with self._lock:
            for key in list(self._online):
                dev = self._devices[key]
                if dev.is_self or key in self._seen_cycle or not (dev.sources & active):
                    continue
                dev.missed += 1
                if dev.missed >= self.missed_threshold:
                    dev.status = Status.OFFLINE
                    self._online.discard(key)
                    gone.append(dev.snapshot())
            self._seen_cycle.clear()
        return gone

    # ---------------------------------------------------------------- search / track mode
    def set_tracked(self, key: str, tracked: bool) -> Optional[Device]:
        """Mark/unmark a device as a search target. Tracking changes nothing about how it is
        scanned - it only makes the registry evaluate TargetAlert (very-close / lost / reappeared)
        for this one device so the UI can surface those without the user watching every row."""
        with self._lock:
            dev = self._devices.get(key)
            if dev is None:
                return None
            dev.tracked = tracked
            if tracked:
                self._tracked.add(key)
                dev.was_close = dev.distance_m is not None and dev.distance_m <= TARGET_CLOSE_THRESHOLD_M
            else:
                self._tracked.discard(key)
            return dev.snapshot()

    def is_tracked(self, key: str) -> bool:
        with self._lock:
            return key in self._tracked

    def enrichment_targets(self, hostname_retry_s: float, probe_refresh_s: float) -> list[tuple[str, str, bool, bool]]:
        """(key, ip, needs_hostname, needs_probe) for online IP devices that are due."""
        now = self._clock()
        out = []
        with self._lock:
            for key in self._online:
                d = self._devices[key]
                if not d.ip or d.transport != "ip" or d.is_self:
                    continue
                need_h = d.hostname is None and now - d.hostname_tried >= hostname_retry_s
                need_p = now - d.last_probe >= probe_refresh_s
                if need_h or need_p:
                    out.append((key, d.ip, need_h, need_p))
        return out

    def set_manual_category(self, key: str, category: Optional[Category]) -> Optional[Device]:
        with self._lock:
            dev = self._devices.get(key)
            if dev is None:
                return None
            old = dev.effective_category
            dev.manual_category = category
            self._set_effective_index(dev, old)
            return dev.snapshot()

    def get(self, key: str) -> Optional[Device]:
        with self._lock:
            dev = self._devices.get(key)
            return dev.snapshot() if dev else None

    def query(self, categories: Optional[Iterable[Category]] = None, text: str = "") -> list[Device]:
        needle = text.strip().lower()
        with self._lock:
            if categories is None:
                keys: Iterable[str] = self._devices.keys()
            else:
                keys = set().union(*(self._cat_index[c] for c in categories)) if categories else set()
            result = []
            for key in keys:
                d = self._devices[key]
                if needle and not any(needle in (v or "").lower() for v in
                                      (d.ip, d.mac, d.hostname, d.vendor, d.display_name)):
                    continue
                result.append(d.snapshot())
        return result

    def counts(self) -> dict[Category, int]:
        """Online devices per effective category."""
        with self._lock:
            return {c: len(idx & self._online) for c, idx in self._cat_index.items()}

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._devices)

    @property
    def online_count(self) -> int:
        with self._lock:
            return len(self._online)

    def clear(self) -> None:
        with self._lock:
            self._devices.clear(); self._by_mac.clear(); self._by_ip.clear()
            self._online.clear(); self._seen_cycle.clear()
            self._filters.clear(); self._trends.clear(); self._tracked.clear()
            for s in self._cat_index.values():
                s.clear()
