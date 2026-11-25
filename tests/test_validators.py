import ipaddress
import unittest

from config.settings import Settings
from utils.validators import (ValidationError, normalize_mac, parse_ipv4, parse_subnet,
                              validate_range, validate_scan_scope)

IFNET = ipaddress.ip_network("192.168.1.0/24")


class TestValidators(unittest.TestCase):
    def test_mac_normalisation(self):
        self.assertEqual(normalize_mac("AA-BB-CC-DD-EE-FF"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(normalize_mac("aabb.ccdd.eeff"), "aa:bb:cc:dd:ee:ff")
        for bad in (None, "", "zz:bb:cc:dd:ee:ff", "aa:bb:cc", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            self.assertIsNone(normalize_mac(bad))

    def test_ipv4(self):
        self.assertEqual(str(parse_ipv4(" 10.0.0.1 ")), "10.0.0.1")
        for bad in ("10.0.0.256", "abc", "", "::1"):
            with self.assertRaises(ValidationError):
                parse_ipv4(bad)

    def test_subnet_parsing(self):
        self.assertEqual(str(parse_subnet("192.168.1.77/24")), "192.168.1.0/24")
        for bad in ("", "192.168.1.0", "192.168.1.0/33", "fe80::/64", "nonsense/24"):
            with self.assertRaises(ValidationError):
                parse_subnet(bad)

    def test_scope_ok_and_narrower(self):
        validate_scan_scope(IFNET, IFNET, 1024)
        validate_scan_scope(ipaddress.ip_network("192.168.1.128/25"), IFNET, 1024)

    def test_scope_rejections(self):
        cases = {
            "public": ipaddress.ip_network("8.8.8.0/24"),
            "outside interface": ipaddress.ip_network("192.168.2.0/24"),
            "wider than interface": ipaddress.ip_network("192.168.0.0/16"),
            "loopback": ipaddress.ip_network("127.0.0.0/24"),
        }
        for label, net in cases.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                validate_scan_scope(net, IFNET, 1024)

    def test_scope_too_large(self):
        big = ipaddress.ip_network("10.0.0.0/16")
        with self.assertRaises(ValidationError):
            validate_scan_scope(big, big, 1024)

    def test_range_and_settings(self):
        self.assertEqual(validate_range("x", "5", 1, 10), 5.0)
        with self.assertRaises(ValidationError):
            validate_range("x", 11, 1, 10)
        with self.assertRaises(ValidationError):
            validate_range("x", "abc", 1, 10)
        s = Settings()
        with self.assertRaises(ValidationError):
            s.update(scan_interval=0.1)
        self.assertEqual(s.scan_interval, 30.0)         # unchanged after failed update
        with self.assertRaises(ValidationError):
            s.update(bogus=1)
        s.update(scan_interval=10)
        self.assertEqual(s.scan_interval, 10)


if __name__ == "__main__":
    unittest.main()
