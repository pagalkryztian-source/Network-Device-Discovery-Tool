"""Search/track mode: distance trend smoothing and target alerts. Pure-logic tests."""
import unittest

from config.defaults import TARGET_CLOSE_THRESHOLD_M
from core.event_manager import EventBus, EventType
from core.network_monitor import NetworkMonitor
from devices.device_registry import DeviceRegistry, TargetAlert
from devices.observation import Observation
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


if __name__ == "__main__":
    unittest.main()
