import threading
import unittest

from devices.device import Category, Status
from devices.device_registry import ChangeKind, DeviceRegistry
from devices.manufacturer_lookup import ManufacturerLookup
from devices.observation import Observation


class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def tick(self, s=1.0): self.t += s


def make(threshold=3):
    clock = FakeClock()
    lookup = ManufacturerLookup(paths=[])          # built-in table only
    return DeviceRegistry(lookup=lookup, missed_threshold=threshold, clock=clock), clock


def arp(ip, mac, **kw):
    return Observation(source="arp", ip=ip, mac=mac, **kw)


class TestRegistry(unittest.TestCase):
    def test_new_then_duplicate(self):
        reg, _ = make()
        r1 = reg.apply(arp("10.0.0.5", "B8:27:EB:00:00:01"))
        r2 = reg.apply(arp("10.0.0.5", "b8:27:eb:00:00:01"))
        self.assertIs(r1.kind, ChangeKind.NEW)
        self.assertIs(r2.kind, ChangeKind.UNCHANGED)
        self.assertEqual(reg.total_count, 1)
        self.assertEqual(r1.device.vendor, "Raspberry Pi Foundation")

    def test_ip_only_record_promoted_to_mac(self):
        reg, _ = make()
        reg.apply(Observation(source="mdns", ip="10.0.0.9", hostname="printer"))
        self.assertEqual(reg.total_count, 1)
        res = reg.apply(arp("10.0.0.9", "00:0c:29:aa:bb:cc"))
        self.assertEqual(reg.total_count, 1)
        self.assertEqual(res.device.key, "00:0c:29:aa:bb:cc")
        self.assertEqual(res.device.hostname, "printer")

    def test_ip_only_record_merged_into_existing_mac_device(self):
        reg, _ = make()
        reg.apply(arp("10.0.0.2", "aa:aa:aa:aa:aa:01"))
        reg.apply(arp("10.0.0.3", "aa:aa:aa:aa:aa:02"))
        reg.apply(Observation(source="mdns", ip="10.0.0.9", hostname="x"))
        res = reg.apply(arp("10.0.0.9", "aa:aa:aa:aa:aa:01"))      # device 01 moved to .9
        self.assertEqual(reg.total_count, 2)
        self.assertEqual(res.device.ip, "10.0.0.9")

    def test_dhcp_reassignment_creates_new_device(self):
        reg, _ = make()
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        res = reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:02"))
        self.assertIs(res.kind, ChangeKind.NEW)
        self.assertEqual(reg.total_count, 2)

    def test_offline_after_threshold_and_reappear(self):
        reg, clock = make(threshold=3)
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        for i in range(2):
            reg.begin_cycle()
            self.assertEqual(reg.end_cycle({"arp"}), [])
        self.assertEqual(reg.online_count, 1)
        reg.begin_cycle()
        gone = reg.end_cycle({"arp"})
        self.assertEqual(len(gone), 1)
        self.assertIs(gone[0].status, Status.OFFLINE)
        self.assertEqual(reg.online_count, 0)
        clock.tick(60)
        res = reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        self.assertIs(res.kind, ChangeKind.REAPPEARED)
        self.assertEqual(res.device.last_seen, clock.t)
        self.assertEqual(reg.online_count, 1)

    def test_seen_device_resets_missed_counter(self):
        reg, _ = make(threshold=2)
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        reg.begin_cycle(); reg.end_cycle({"arp"})                 # missed = 1
        reg.begin_cycle(); reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        self.assertEqual(reg.end_cycle({"arp"}), [])
        reg.begin_cycle()
        self.assertEqual(reg.end_cycle({"arp"}), [])              # missed = 1 again, below threshold

    def test_failed_protocol_never_marks_offline(self):
        reg, _ = make(threshold=1)
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        reg.begin_cycle()
        self.assertEqual(reg.end_cycle(set()), [])                # ARP failed this cycle
        self.assertEqual(reg.online_count, 1)

    def test_other_protocol_sighting_keeps_device_online(self):
        reg, _ = make(threshold=1)
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        reg.begin_cycle()
        reg.apply(Observation(source="mdns", ip="10.0.0.5", hostname="tv"))   # ARP missed it, mDNS saw it
        self.assertEqual(reg.end_cycle({"arp", "mdns"}), [])

    def test_hostname_lookup_does_not_confirm_presence(self):
        reg, clock = make()
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        seen = reg.get("aa:aa:aa:aa:aa:01").last_seen
        clock.tick(100)
        reg.apply(Observation(source="hostname", ip="10.0.0.5", hostname="box", confirms_presence=False))
        self.assertEqual(reg.get("aa:aa:aa:aa:aa:01").last_seen, seen)

    def test_self_device_never_offline(self):
        reg, _ = make(threshold=1)
        reg.apply(arp("10.0.0.1", "aa:aa:aa:aa:aa:09", is_self=True))
        reg.begin_cycle()
        self.assertEqual(reg.end_cycle({"arp"}), [])

    def test_counts_filter_search_manual(self):
        reg, _ = make()
        reg.apply(arp("10.0.0.5", "b8:27:eb:00:00:01"))
        reg.apply(Observation(source="mdns", ip="10.0.0.6", hostname="office-printer", mdns_services={"_ipp._tcp"}))
        counts = reg.counts()
        self.assertEqual(counts[Category.PRINTER], 1)
        self.assertEqual(len(reg.query([Category.PRINTER])), 1)
        self.assertEqual(len(reg.query(text="raspberry")), 1)
        self.assertEqual(len(reg.query(text="10.0.0.6")), 1)
        self.assertEqual(reg.query([]), [])
        snap = reg.set_manual_category("b8:27:eb:00:00:01", Category.CCTV_CAMERA)
        self.assertIs(snap.effective_category, Category.CCTV_CAMERA)
        self.assertEqual(reg.counts()[Category.CCTV_CAMERA], 1)
        reg.set_manual_category("b8:27:eb:00:00:01", None)
        self.assertEqual(reg.counts()[Category.CCTV_CAMERA], 0)
        self.assertIsNone(reg.set_manual_category("nope", None))

    def test_snapshots_are_isolated(self):
        reg, _ = make()
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01", ))
        snap = reg.get("aa:aa:aa:aa:aa:01")
        snap.hostname = "tampered"; snap.sources.add("x")
        self.assertIsNone(reg.get("aa:aa:aa:aa:aa:01").hostname)
        self.assertNotIn("x", reg.get("aa:aa:aa:aa:aa:01").sources)

    def test_observation_requires_identity(self):
        reg, _ = make()
        with self.assertRaises(ValueError):
            reg.apply(Observation(source="arp"))

    def test_clear(self):
        reg, _ = make()
        reg.apply(arp("10.0.0.5", "aa:aa:aa:aa:aa:01"))
        reg.clear()
        self.assertEqual((reg.total_count, reg.online_count), (0, 0))
        self.assertEqual(sum(reg.counts().values()), 0)

    def test_thread_safety(self):
        reg, _ = make()
        errors = []

        def worker(offset):
            try:
                for i in range(200):
                    n = (offset * 200 + i) % 120          # heavy overlap between threads
                    reg.apply(arp(f"10.0.{n // 250}.{n % 250 + 1}", f"02:00:00:00:{n // 256:02x}:{n % 256:02x}"))
                    reg.query(text="10.0")
                    reg.counts()
            except Exception as exc:                      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(reg.total_count, 120)
        self.assertEqual(reg.online_count, 120)
        self.assertEqual(sum(reg.counts().values()), 120)


if __name__ == "__main__":
    unittest.main()
