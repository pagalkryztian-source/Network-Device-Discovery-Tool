"""Passive radio features: path-loss maths, Kalman smoothing, `iw` / `bluetoothctl` parsing,
registry signal handling, radio-only monitor mode and radar geometry."""
import unittest

from config.settings import Settings
from core.event_manager import EventBus, EventType
from core.network_monitor import NetworkMonitor
from devices.device import Category
from devices.device_registry import DeviceRegistry
from devices.manufacturer_lookup import ManufacturerLookup
from devices.observation import Observation
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from discovery.bluetooth_discovery import parse_bluetoothctl_rssi
from discovery.wifi_discovery import WiFiDiscovery, parse_iw_interfaces, parse_iw_scan
from ui.radar_math import SECTORS, UNMEASURED_BAND, blip_position, distance_frac
from utils.signal import (KalmanRssi, PathLossModel, decode_ssid, distance_range, format_distance, median,
                          rssi_to_distance, wifi_band, wifi_channel)

MODEL = PathLossModel(-40.0, 2.7)

IW_DUMP = """BSS 9c:5c:8e:aa:bb:cc(on wlan0) -- associated
\tlast seen: 100.0s [boottime]
\tTSF: 1 usec
\tfreq: 2437
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -52.00 dBm
\tlast seen: 120 ms ago
\tSSID: HomeNet
\tDS Parameter set: channel 6
\tRSN:\t * Version: 1
\t\t * Authentication suites: PSK
BSS 11:22:33:44:55:66(on wlan0)
\tfreq: 5180
\tcapability: ESS Privacy (0x0011)
\tsignal: -71.00 dBm
\tlast seen: 800 ms ago
\tSSID: \\x00\\x00\\x00
\tRSN:\t * Version: 1
\t\t * Authentication suites: SAE
BSS aa:aa:aa:aa:aa:aa(on wlan0)
\tfreq: 2412
\tcapability: ESS (0x0401)
\tsignal: -80.00 dBm
\tlast seen: 99000 ms ago
\tSSID: Stale
BSS de:ad:be:ef:00:01(on wlan0)
\tfreq: 2462
\tcapability: ESS (0x0401)
\tsignal: -60.00 dBm
\tlast seen: 50 ms ago
\tSSID: Caf\\xc3\\xa9 Free
"""


class TestSignalMath(unittest.TestCase):
    def test_distance_at_reference_is_one_metre(self):
        self.assertAlmostEqual(rssi_to_distance(-40.0, MODEL), 1.0, places=6)

    def test_weaker_signal_is_farther(self):
        self.assertLess(rssi_to_distance(-50, MODEL), rssi_to_distance(-60, MODEL))
        self.assertLess(rssi_to_distance(-60, MODEL), rssi_to_distance(-80, MODEL))

    def test_ten_n_db_is_a_factor_of_ten(self):
        self.assertAlmostEqual(rssi_to_distance(-40 - 27, MODEL) / rssi_to_distance(-40, MODEL), 10.0, places=6)

    def test_clamped(self):
        self.assertEqual(rssi_to_distance(-10, MODEL), 0.3)
        self.assertEqual(rssi_to_distance(-120, MODEL), 200.0)

    def test_range_brackets_estimate_and_is_honest(self):
        lo, hi = distance_range(5.0, MODEL)
        self.assertLess(lo, 5.0)
        self.assertGreater(hi, 5.0)
        self.assertGreater(hi / lo, 1.5)                  # never pretends to be exact
        self.assertEqual(format_distance(None, MODEL), "-")
        self.assertIn("m (", format_distance(5.0, MODEL))

    def test_median_and_channels(self):
        self.assertEqual(median([3, 1, 2]), 2.0)
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
        self.assertEqual((wifi_channel(2412), wifi_channel(2484), wifi_channel(5180), wifi_channel(5955)), (1, 14, 36, 1))
        self.assertEqual((wifi_band(2437), wifi_band(5180), wifi_band(6000)), ("2.4 GHz", "5 GHz", "6 GHz"))
        self.assertIsNone(wifi_channel(None))

    def test_decode_ssid(self):
        self.assertEqual(decode_ssid("Caf\\xc3\\xa9 Free"), "Café Free")
        self.assertIsNone(decode_ssid("\\x00\\x00\\x00"))
        self.assertIsNone(decode_ssid(""))


class TestKalman(unittest.TestCase):
    def test_converges_and_damps_noise(self):
        f = KalmanRssi()
        noisy = [-60, -70, -55, -65, -62, -68, -58, -64, -61, -66] * 3
        out = [f.update(v) for v in noisy]
        self.assertEqual(out[0], -60)
        self.assertLess(max(out[10:]) - min(out[10:]), max(noisy) - min(noisy))      # smoother than raw
        self.assertAlmostEqual(out[-1], sum(noisy) / len(noisy), delta=3.5)

    def test_follows_a_real_change(self):
        f = KalmanRssi()
        for _ in range(10):
            f.update(-50)
        for _ in range(15):
            last = f.update(-75)
        self.assertLess(last, -70)


class TestParsers(unittest.TestCase):
    def test_iw_scan(self):
        got = {o.mac: o for o in parse_iw_scan(IW_DUMP)}
        self.assertEqual(set(got), {"9c:5c:8e:aa:bb:cc", "11:22:33:44:55:66", "de:ad:be:ef:00:01"})   # stale one dropped
        home = got["9c:5c:8e:aa:bb:cc"]
        self.assertEqual((home.ssid, home.rssi, home.channel, home.band, home.security, home.transport),
                         ("HomeNet", -52, 6, "2.4 GHz", "WPA2", "wifi"))
        hidden = got["11:22:33:44:55:66"]
        self.assertEqual((hidden.ssid, hidden.security, hidden.channel), (None, "WPA3", 36))
        self.assertEqual(got["de:ad:be:ef:00:01"].ssid, "Café Free")
        self.assertEqual(got["de:ad:be:ef:00:01"].security, "Open")

    def test_iw_garbage(self):
        self.assertEqual(parse_iw_scan("nonsense\n\x00\x01"), [])
        self.assertEqual(parse_iw_interfaces("phy#0\n\tInterface wlan0\n\t\tifindex 3\n"), ["wlan0"])

    def test_bluetooth_rssi_median_and_formats(self):
        out = ("[CHG] Device 4C:AA:BB:CC:DD:EE RSSI: 0xffffffb7 (-73)\n"
               "\x1b[0;94m[CHG]\x1b[0m Device 4C:AA:BB:CC:DD:EE RSSI: -67\n"
               "[CHG] Device 4C:AA:BB:CC:DD:EE RSSI: -69\n"
               "[CHG] Device 4C:AA:BB:CC:DD:EE RSSI: 999\n"           # impossible value ignored
               "[CHG] Device 11:22:33:44:55:66 RSSI: -80\n")
        self.assertEqual(parse_bluetoothctl_rssi(out), {"4c:aa:bb:cc:dd:ee": -69, "11:22:33:44:55:66": -80})

    def test_wifi_discovery_end_to_end_with_fake_runner(self):
        calls = []

        def runner(cmd, timeout):
            calls.append(cmd)
            if cmd[:2] == ["iw", "dev"] and len(cmd) == 2:
                return "phy#0\n\tInterface wlan0\n"
            if cmd[0] == "iw":
                return IW_DUMP
            return ""
        disc = WiFiDiscovery(runner=runner)
        import threading
        res = disc.discover(DiscoveryContext(), threading.Event())
        self.assertEqual(len(res), 3)
        self.assertFalse(disc.requires_network)

    def test_wifi_discovery_reports_missing_adapter(self):
        disc = WiFiDiscovery(runner=lambda cmd, t: "phy#0\n")
        import threading
        with self.assertRaises(DiscoveryError):
            disc.discover(DiscoveryContext(), threading.Event())


class TestRegistrySignal(unittest.TestCase):
    def setUp(self):
        self.reg = DeviceRegistry(ManufacturerLookup([]), missed_threshold=2)
        self.reg.set_signal_models(MODEL, PathLossModel(-59, 2.5))

    def wifi(self, rssi, mac="9c:5c:8e:aa:bb:cc"):
        return Observation(source="wifi", mac=mac, transport="wifi", rssi=rssi, ssid="HomeNet",
                           channel=6, band="2.4 GHz", security="WPA2")

    def test_wifi_ap_gets_distance_ssid_and_type(self):
        res = self.reg.apply(self.wifi(-52))
        d = res.device
        self.assertEqual((d.ssid, d.channel, d.rssi_source), ("HomeNet", 6, "wifi"))
        self.assertAlmostEqual(d.distance_m, rssi_to_distance(-52, MODEL), places=6)
        self.assertEqual(d.effective_category, Category.WIFI_AP)
        self.assertEqual(d.display_name, "HomeNet")

    def test_smoothing_across_readings(self):
        for v in (-50, -70, -50, -70):
            d = self.reg.apply(self.wifi(v)).device
        self.assertTrue(-70 < d.rssi < -50)
        self.assertEqual(d.rssi_raw, -70)

    def test_bluetooth_uses_its_own_model(self):
        d = self.reg.apply(Observation(source="bluetooth", mac="4c:aa:bb:cc:dd:ee", transport="bluetooth",
                                       bt_name="Galaxy Watch", rssi=-59)).device
        self.assertAlmostEqual(d.distance_m, 1.0, places=6)
        self.assertEqual(d.effective_category, Category.SMARTWATCH)

    def test_bluetooth_unnamed_is_generic_bluetooth(self):
        d = self.reg.apply(Observation(source="bluetooth", mac="4c:aa:bb:cc:dd:ee", transport="bluetooth", rssi=-70)).device
        self.assertEqual(d.effective_category, Category.BLUETOOTH)

    def test_changing_models_changes_distance(self):
        self.reg.apply(self.wifi(-60))
        self.reg.set_signal_models(PathLossModel(-30, 2.0), PathLossModel(-59, 2.5))
        self.assertGreater(self.reg.apply(self.wifi(-60)).device.distance_m, 20)

    def test_lan_device_without_signal_has_no_distance(self):
        d = self.reg.apply(Observation(source="arp", ip="192.168.1.5", mac="aa:aa:aa:aa:aa:05")).device
        self.assertIsNone(d.distance_m)
        self.assertIsNone(d.rssi)

    def test_router_seen_by_arp_and_wifi_merges_with_distance(self):
        self.reg.apply(Observation(source="arp", ip="192.168.1.1", mac="9c:5c:8e:aa:bb:cc", is_gateway=True))
        d = self.reg.apply(self.wifi(-45)).device
        self.assertEqual((d.ip, d.ssid), ("192.168.1.1", "HomeNet"))
        self.assertIsNotNone(d.distance_m)
        self.assertEqual(self.reg.total_count, 1)


class FakeRadio(BaseDiscovery):
    name, requires_network = "wifi", False

    def __init__(self, scripts):
        self.scripts, self.calls = scripts, 0

    def discover(self, ctx, cancel):
        step = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        return step


class NeedsNet(BaseDiscovery):
    name = "arp"

    def __init__(self):
        self.calls = 0

    def discover(self, ctx, cancel):
        self.calls += 1
        return []


class TestRadioOnlyMonitor(unittest.TestCase):
    def test_runs_without_any_network_and_skips_ip_modules(self):
        s = Settings(missed_threshold=2)
        bus = EventBus()
        reg = DeviceRegistry(ManufacturerLookup([]), missed_threshold=2)
        radio = FakeRadio([[Observation(source="wifi", mac="9c:5c:8e:aa:bb:cc", transport="wifi", rssi=-55, ssid="X")]])
        net = NeedsNet()
        mon = NetworkMonitor(reg, bus, [net, radio], s)
        mon._ctx = DiscoveryContext()                       # no interface, no network
        self.assertTrue(mon.run_cycle())
        self.assertEqual(net.calls, 0)                      # ARP never ran: nothing to scan
        events = [e.type for e in bus.drain()]
        self.assertIn(EventType.DEVICE_DISCOVERED, events)
        self.assertIn(EventType.CYCLE_COMPLETED, events)
        self.assertNotIn(EventType.SCAN_ERROR, events)
        self.assertEqual(reg.online_count, 1)

    def test_access_point_that_disappears_goes_offline(self):
        s = Settings(missed_threshold=2)
        bus, reg = EventBus(), DeviceRegistry(ManufacturerLookup([]), missed_threshold=2)
        ap = Observation(source="wifi", mac="9c:5c:8e:aa:bb:cc", transport="wifi", rssi=-55, ssid="X")
        radio = FakeRadio([[ap], [], []])
        mon = NetworkMonitor(reg, bus, [radio], s)
        mon._ctx = DiscoveryContext()
        for _ in range(3):
            mon.run_cycle()
        self.assertEqual(reg.online_count, 0)


class TestRadarGeometry(unittest.TestCase):
    def test_distance_is_monotonic_and_bounded(self):
        fr = [distance_frac(d) for d in (0.3, 1, 3, 10, 30, 50, 500)]
        self.assertEqual(fr, sorted(fr))
        self.assertTrue(all(0.0 < f <= 0.8 for f in fr))

    def test_measured_device_radius_tracks_distance(self):
        _, near = blip_position("k", "wifi", 1.0)
        _, far = blip_position("k", "wifi", 25.0)
        self.assertLess(near, far)

    def test_unmeasured_devices_sit_in_the_unknown_band(self):
        lo, hi = UNMEASURED_BAND
        for i in range(50):
            _, r = blip_position(f"aa:{i:02x}", "ip", None)
            self.assertTrue(lo <= r <= hi)

    def test_angle_stays_in_its_sector_and_is_stable(self):
        for sector, (start, width, _l) in SECTORS.items():
            for i in range(50):
                a, _ = blip_position(f"k{i}", sector, 5.0)
                self.assertTrue(start < a < start + width)
        self.assertEqual(blip_position("x", "wifi", 5.0), blip_position("x", "wifi", 5.0))


if __name__ == "__main__":
    unittest.main()
