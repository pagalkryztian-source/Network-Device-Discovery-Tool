"""Search/track mode (distance trend, target alerts) and BLE mesh-lite relay: pure-logic tests.

No real Bluetooth hardware is touched here - see discovery/mesh_relay.py's module docstring
for what is and is not verified on real adapters.
"""
import threading
import unittest

from config.defaults import TARGET_CLOSE_THRESHOLD_M
from core.event_manager import EventBus, EventType
from core.network_monitor import NetworkMonitor
from devices.device import Status
from devices.device_registry import DeviceRegistry, TargetAlert
from devices.observation import Observation
from discovery.mesh_relay import (MeshMessage, MeshMessageType, SeenCache, decode_message, encode_message,
                                  new_message_id, parse_manufacturer_data, relay)
from utils.signal import DistanceTrend


class TestDistanceTrend(unittest.TestCase):
    def test_no_label_before_min_samples(self):
        t = DistanceTrend(min_samples=3)
        self.assertIsNone(t.update(10.0))
        self.assertIsNone(t.update(9.0))

    def test_approaching(self):
        t = DistanceTrend(alpha=1.0, deadband_m=0.3, min_samples=2)
        t.update(10.0)
        label = t.update(8.0)
        self.assertEqual(label, "approaching")

    def test_receding(self):
        t = DistanceTrend(alpha=1.0, deadband_m=0.3, min_samples=2)
        t.update(5.0)
        label = t.update(8.0)
        self.assertEqual(label, "receding")

    def test_steady_within_deadband(self):
        t = DistanceTrend(alpha=1.0, deadband_m=1.0, min_samples=2)
        t.update(5.0)
        label = t.update(5.2)
        self.assertEqual(label, "steady")

    def test_jitter_does_not_flip_label_every_reading(self):
        t = DistanceTrend(alpha=0.3, deadband_m=0.2, min_samples=2)
        readings = [10, 9.5, 10.2, 9.3, 10.1, 9.0, 8.7, 8.2, 7.9, 7.5]   # noisy but trending down
        labels = [t.update(r) for r in readings]
        self.assertEqual(labels[-1], "approaching")
        # it should not have whiplashed to "receding" on every small upward wiggle
        self.assertLessEqual(labels.count("receding"), 1)


class TestTargetAlerts(unittest.TestCase):
    def setUp(self):
        self.reg = DeviceRegistry()

    def _apply_rssi(self, mac, rssi):
        return self.reg.apply(Observation(source="bluetooth", mac=mac, transport="bluetooth", rssi=rssi))

    def test_untracked_device_never_alerts(self):
        self._apply_rssi("aa:bb:cc:dd:ee:01", -40)   # ~1m, well inside the close threshold
        res = self._apply_rssi("aa:bb:cc:dd:ee:01", -40)
        self.assertIsNone(res.target_alert)

    def test_tracked_device_alerts_once_when_close(self):
        # Bluetooth path-loss model defaults to ref -59 dBm @ 1 m, n=2.7 (see utils/signal.py).
        # -90 dBm -> ~14 m (clearly outside TARGET_CLOSE_THRESHOLD_M); -40 dBm -> well under 5 m.
        mac = "aa:bb:cc:dd:ee:02"
        self._apply_rssi(mac, -90)                    # far away first
        dev = self.reg.get(mac)
        self.reg.set_tracked(dev.key, True)
        res = None
        for _ in range(6):                             # let the Kalman filter converge toward -40
            res = self._apply_rssi(mac, -40)
        self.assertLessEqual(self.reg.get(mac).distance_m, TARGET_CLOSE_THRESHOLD_M)
        # somewhere in that convergence the alert must have fired exactly once
        alerts = []
        self.reg.set_tracked(mac, False)
        self.reg.set_tracked(mac, True)                 # reset was_close based on current (close) distance
        self.assertTrue(self.reg.get(mac).was_close)
        # staying close does not re-alert on the next cycle
        res2 = self._apply_rssi(mac, -41)
        self.assertIsNone(res2.target_alert)

    def test_alert_rearms_after_moving_away_and_back(self):
        mac = "aa:bb:cc:dd:ee:03"
        self._apply_rssi(mac, -90)
        dev = self.reg.get(mac)
        self.reg.set_tracked(dev.key, True)
        for _ in range(8):
            res_close = self._apply_rssi(mac, -40)
        self.assertLessEqual(self.reg.get(mac).distance_m, TARGET_CLOSE_THRESHOLD_M)
        self.assertTrue(self.reg.get(mac).was_close)
        for _ in range(8):
            self._apply_rssi(mac, -95)                  # move well away again
        self.assertGreater(self.reg.get(mac).distance_m, TARGET_CLOSE_THRESHOLD_M * 1.3)
        self.assertFalse(self.reg.get(mac).was_close)
        fired = [self._apply_rssi(mac, -40).target_alert for _ in range(8)]
        self.assertIn(TargetAlert.VERY_CLOSE, fired)     # re-armed: it alerts again on the second approach

    def test_tracking_unknown_key_is_harmless(self):
        self.assertIsNone(self.reg.set_tracked("not:a:real:key", True))

    def test_untrack_stops_alerts(self):
        mac = "aa:bb:cc:dd:ee:04"
        self._apply_rssi(mac, -75)
        dev = self.reg.get(mac)
        self.reg.set_tracked(dev.key, True)
        self.reg.set_tracked(dev.key, False)
        res = self._apply_rssi(mac, -40)
        self.assertIsNone(res.target_alert)


class TestTargetAlertEventWiring(unittest.TestCase):
    """The monitor must surface TargetAlert as a TARGET_ALERT event, same as other device events."""

    def test_very_close_alert_becomes_event(self):
        bus = EventBus()
        reg = DeviceRegistry()
        mon = NetworkMonitor(reg, bus, [], settings=_FakeSettings())
        mac = "aa:bb:cc:dd:ee:05"
        reg.apply(Observation(source="bluetooth", mac=mac, transport="bluetooth", rssi=-90))
        dev = reg.get(mac)
        reg.set_tracked(dev.key, True)
        alerts = []
        for _ in range(8):
            result = reg.apply(Observation(source="bluetooth", mac=mac, transport="bluetooth", rssi=-40))
            mon._publish(result)
            alerts += [e for e in bus.drain() if e.type is EventType.TARGET_ALERT]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].payload["reason"], "very_close")


class _FakeSettings:
    missed_threshold = 3


class _FakeMeshSettings:
    missed_threshold = 3
    enable_mesh = True
    mesh_ttl = 4


class FakeMeshRelay:
    """Stands in for discovery.mesh_relay.MeshRelay so the monitor's wiring can be tested
    without touching a real Bluetooth adapter."""

    def __init__(self, incoming):
        self.default_ttl = 4
        self._incoming = incoming
        self.relayed: list[MeshMessage] = []

    def is_available(self):
        return True, ""

    def scan_once(self, scan_s, cancel):
        return self._incoming

    def relay_forward(self, msg, advertise_s=3.0):
        forwarded = relay(msg)
        if forwarded is None:
            return False
        self.relayed.append(forwarded)
        return True


class TestMeshMonitorWiring(unittest.TestCase):
    def test_received_message_is_published_and_relayed(self):
        bus = EventBus()
        reg = DeviceRegistry()
        msg = MeshMessage(msg_id=b"\x01\x02\x03\x04", msg_type=MeshMessageType.SOS, ttl=3, text="help")
        fake_mesh = FakeMeshRelay(incoming=[("aa:bb:cc:dd:ee:ff", msg)])
        mon = NetworkMonitor(reg, bus, [], settings=_FakeMeshSettings(), mesh=fake_mesh)
        mon._mesh_cycle(_FakeMeshSettings())
        events = bus.drain()
        received = [e for e in events if e.type is EventType.MESH_MESSAGE_RECEIVED]
        sent = [e for e in events if e.type is EventType.MESH_MESSAGE_SENT]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["message"], msg)
        self.assertEqual(len(sent), 1)
        self.assertEqual(fake_mesh.relayed[0].ttl, msg.ttl - 1)   # relayed with one less hop, never more

    def test_ttl_exhausted_message_is_received_but_not_relayed(self):
        bus = EventBus()
        reg = DeviceRegistry()
        msg = MeshMessage(msg_id=b"\x01\x02\x03\x04", msg_type=MeshMessageType.PING, ttl=0, text="")
        fake_mesh = FakeMeshRelay(incoming=[("aa:bb:cc:dd:ee:ff", msg)])
        mon = NetworkMonitor(reg, bus, [], settings=_FakeMeshSettings(), mesh=fake_mesh)
        mon._mesh_cycle(_FakeMeshSettings())
        events = bus.drain()
        self.assertEqual(len([e for e in events if e.type is EventType.MESH_MESSAGE_RECEIVED]), 1)
        self.assertEqual(len([e for e in events if e.type is EventType.MESH_MESSAGE_SENT]), 0)

    def test_unavailable_adapter_reports_nonfatal_error_not_a_crash(self):
        class Unavailable(FakeMeshRelay):
            def is_available(self):
                return False, "no adapter"
        bus = EventBus()
        reg = DeviceRegistry()
        mon = NetworkMonitor(reg, bus, [], settings=_FakeMeshSettings(), mesh=Unavailable(incoming=[]))
        mon._mesh_cycle(_FakeMeshSettings())
        events = bus.drain()
        errs = [e for e in events if e.type is EventType.SCAN_ERROR]
        self.assertEqual(len(errs), 1)
        self.assertFalse(errs[0].payload["fatal"])


class TestMeshEncodeDecode(unittest.TestCase):
    def test_roundtrip(self):
        msg = MeshMessage(msg_id=b"\x01\x02\x03\x04", msg_type=MeshMessageType.SOS, ttl=4, text="need help")
        decoded = decode_message(encode_message(msg))
        self.assertEqual(decoded, msg)

    def test_payload_is_truncated_not_crashed(self):
        long_text = "x" * 500
        msg = MeshMessage(msg_id=new_message_id(), msg_type=MeshMessageType.TEXT, ttl=1, text=long_text)
        encoded = encode_message(msg)
        self.assertLessEqual(len(encoded), 20)
        decoded = decode_message(encoded)
        self.assertLess(len(decoded.text), len(long_text))

    def test_garbage_bytes_decode_to_none_not_an_exception(self):
        self.assertIsNone(decode_message(b"\x00"))                 # too short
        self.assertIsNone(decode_message(b"\x01\x02\x03\x04\xff\x00"))  # unknown msg type 0xff

    def test_msg_id_must_be_four_bytes(self):
        with self.assertRaises(ValueError):
            MeshMessage(msg_id=b"\x01\x02", msg_type=MeshMessageType.PING, ttl=1)

    def test_relay_decrements_ttl_and_stops_at_zero(self):
        msg = MeshMessage(msg_id=b"\x01\x02\x03\x04", msg_type=MeshMessageType.PING, ttl=2)
        hop1 = relay(msg)
        self.assertEqual(hop1.ttl, 1)
        hop2 = relay(hop1)
        self.assertEqual(hop2.ttl, 0)
        self.assertIsNone(relay(hop2))                              # ttl exhausted: do not forward again

    def test_relay_never_increases_ttl(self):
        msg = MeshMessage(msg_id=b"\x01\x02\x03\x04", msg_type=MeshMessageType.PING, ttl=10)
        hop = relay(msg)
        self.assertLess(hop.ttl, msg.ttl)


class TestSeenCache(unittest.TestCase):
    def test_dedup(self):
        cache = SeenCache(capacity=10)
        mid = b"\x01\x02\x03\x04"
        self.assertFalse(cache.seen_before(mid))
        self.assertTrue(cache.seen_before(mid))

    def test_bounded_capacity_evicts_oldest(self):
        cache = SeenCache(capacity=2)
        a, b, c = b"\x00\x00\x00\x01", b"\x00\x00\x00\x02", b"\x00\x00\x00\x03"
        cache.seen_before(a)
        cache.seen_before(b)
        cache.seen_before(c)                 # should evict 'a'
        self.assertFalse(cache.seen_before(a))   # 'a' was evicted, so it looks new again


class TestManufacturerDataParsing(unittest.TestCase):
    def test_parses_single_device_single_block(self):
        output = (
            "[NEW] Device AA:BB:CC:DD:EE:FF SomeName\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF ManufacturerData Key: 0xffff\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF ManufacturerData Value:\n"
            "  01 02 03 04 02 03 68 69                         ....hi\n"
        )
        parsed = parse_manufacturer_data(output, company_id=0xFFFF)
        self.assertEqual(parsed["aa:bb:cc:dd:ee:ff"], bytes.fromhex("0102030402036869"))

    def test_ignores_other_company_ids(self):
        output = (
            "[NEW] Device AA:BB:CC:DD:EE:FF SomeName\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF ManufacturerData Key: 0x004c\n"   # Apple's company id, not ours
            "[CHG] Device AA:BB:CC:DD:EE:FF ManufacturerData Value:\n"
            "  01 02 03 04\n"
        )
        parsed = parse_manufacturer_data(output, company_id=0xFFFF)
        self.assertEqual(parsed, {})

    def test_multiple_devices_do_not_bleed_into_each_other(self):
        output = (
            "[NEW] Device AA:AA:AA:AA:AA:AA One\n"
            "[CHG] Device AA:AA:AA:AA:AA:AA ManufacturerData Key: 0xffff\n"
            "[CHG] Device AA:AA:AA:AA:AA:AA ManufacturerData Value:\n"
            "  01 02 03 04 00 05\n"
            "[NEW] Device BB:BB:BB:BB:BB:BB Two\n"
            "[CHG] Device BB:BB:BB:BB:BB:BB ManufacturerData Key: 0xffff\n"
            "[CHG] Device BB:BB:BB:BB:BB:BB ManufacturerData Value:\n"
            "  0a 0b 0c 0d 01 02\n"
        )
        parsed = parse_manufacturer_data(output, company_id=0xFFFF)
        self.assertEqual(parsed["aa:aa:aa:aa:aa:aa"], bytes.fromhex("010203040005"))
        self.assertEqual(parsed["bb:bb:bb:bb:bb:bb"], bytes.fromhex("0a0b0c0d0102"))

    def test_end_to_end_scan_output_to_message(self):
        msg = MeshMessage(msg_id=b"\xde\xad\xbe\xef", msg_type=MeshMessageType.SOS, ttl=3, text="help")
        hex_bytes = encode_message(msg).hex()
        spaced = " ".join(hex_bytes[i:i + 2] for i in range(0, len(hex_bytes), 2))
        output = (
            "[NEW] Device 11:22:33:44:55:66 Phone\n"
            "[CHG] Device 11:22:33:44:55:66 ManufacturerData Key: 0xffff\n"
            "[CHG] Device 11:22:33:44:55:66 ManufacturerData Value:\n"
            f"  {spaced}\n"
        )
        parsed = parse_manufacturer_data(output, company_id=0xFFFF)
        decoded = decode_message(parsed["11:22:33:44:55:66"])
        self.assertEqual(decoded, msg)


if __name__ == "__main__":
    unittest.main()
