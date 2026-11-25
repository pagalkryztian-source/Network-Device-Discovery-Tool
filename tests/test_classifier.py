import unittest

from devices.device import Category as C, Confidence, Device
from devices.device_classifier import DeviceClassifier

clf = DeviceClassifier()


def dev(**kw):
    return Device(key="k", **kw)


class TestClassifier(unittest.TestCase):
    def test_no_evidence_is_unknown(self):
        r = clf.classify(dev())
        self.assertIs(r.category, C.UNKNOWN)
        self.assertIs(r.confidence, Confidence.UNKNOWN)
        self.assertTrue(r.reasons)

    def test_vendor_alone_is_never_high(self):
        r = clf.classify(dev(vendor="Hangzhou Hikvision Digital Technology"))
        self.assertIs(r.category, C.CCTV_CAMERA)
        self.assertNotEqual(r.confidence, Confidence.HIGH)
        self.assertIs(r.confidence, Confidence.LOW)
        self.assertIn("manufacturer", r.reasons[0])

    def test_vendor_plus_hostname_plus_port_is_high(self):
        r = clf.classify(dev(vendor="Hikvision", hostname="ipcam-garage", open_ports={554}))
        self.assertIs(r.category, C.CCTV_CAMERA)
        self.assertIs(r.confidence, Confidence.HIGH)
        self.assertGreaterEqual(len(r.reasons), 3)

    def test_vendor_plus_hostname_is_medium(self):
        r = clf.classify(dev(vendor="Hikvision", hostname="ipcam-garage"))
        self.assertIs(r.confidence, Confidence.MEDIUM)

    def test_ipp_service_is_high_printer(self):
        r = clf.classify(dev(mdns_services={"_ipp._tcp"}))
        self.assertIs(r.category, C.PRINTER)
        self.assertIs(r.confidence, Confidence.HIGH)

    def test_ssdp_gateway_device(self):
        r = clf.classify(dev(ssdp_info={"st": "urn:schemas-upnp-org:device:InternetGatewayDevice:1"}))
        self.assertIs(r.category, C.ROUTER)
        self.assertIs(r.confidence, Confidence.HIGH)

    def test_default_gateway_signal(self):
        r = clf.classify(dev(is_gateway=True))
        self.assertIs(r.category, C.ROUTER)
        self.assertIs(r.confidence, Confidence.HIGH)

    def test_hostname_patterns(self):
        cases = {"Johns-iPhone": C.SMARTPHONE, "galaxy-watch5": C.SMARTWATCH, "LAPTOP-AB12": C.COMPUTER,
                 "living-room-tv": C.SMART_TV, "ipcam-01": C.CCTV_CAMERA, "HP1A2B3C": C.PRINTER,
                 "openwrt": C.ROUTER, "diskstation": C.NETWORK_STORAGE, "esp32-1a2b3c": C.IOT_DEVICE}
        for name, expected in cases.items():
            with self.subTest(name):
                self.assertIs(clf.classify(dev(hostname=name)).category, expected)

    def test_substring_does_not_cause_false_tv_match(self):
        self.assertIsNot(clf.classify(dev(hostname="activation-server")).category, C.SMART_TV)

    def test_randomised_mac_is_weak_phone_hint_only(self):
        r = clf.classify(dev(randomized_mac=True))
        self.assertIs(r.category, C.SMARTPHONE)
        self.assertIs(r.confidence, Confidence.LOW)

    def test_conflicting_signals_are_demoted(self):
        r = clf.classify(dev(hostname="laptop-1", open_ports={554}))   # computer 50 vs camera 50
        self.assertIsNot(r.confidence, Confidence.HIGH)

    def test_self(self):
        r = clf.classify(dev(is_self=True))
        self.assertIs(r.category, C.COMPUTER)

    def test_bluetooth_name(self):
        self.assertIs(clf.classify(dev(bt_name="Fitbit Charge 5")).category, C.SMARTWATCH)

    def test_manual_override_wins_in_effective_category(self):
        d = dev(vendor="Hikvision")
        d.category = C.CCTV_CAMERA
        d.manual_category = C.IOT_DEVICE
        self.assertIs(d.effective_category, C.IOT_DEVICE)


if __name__ == "__main__":
    unittest.main()
