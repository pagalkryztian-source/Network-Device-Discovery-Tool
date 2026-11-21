import ipaddress
import os
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS

from devices.manufacturer_lookup import ManufacturerLookup
from network.interface_manager import InterfaceError, InterfaceInfo, NetworkInterfaceManager
from network.network_utils import (get_default_gateway, has_raw_socket_privilege, is_locally_administered,
                                   probe_ports)
from network.subnet_manager import SubnetManager
from utils.rate_limiter import RateLimiter
from utils.validators import ValidationError

AF_LINK = 17


class FakePsutil:
    AF_LINK = AF_LINK

    def net_if_addrs(self):
        return {
            "lo": [NS(family=socket.AF_INET, address="127.0.0.1", netmask="255.0.0.0")],
            "eth0": [NS(family=socket.AF_INET, address="192.168.1.10", netmask="255.255.255.0"),
                     NS(family=AF_LINK, address="aa:bb:cc:dd:ee:ff")],
            "wlan0": [NS(family=socket.AF_INET, address="10.0.0.4", netmask="255.255.0.0")],
            "down0": [NS(family=socket.AF_INET, address="172.16.0.2", netmask="255.255.255.0")],
            "noip": [NS(family=AF_LINK, address="aa:aa:aa:aa:aa:aa")],
        }

    def net_if_stats(self):
        return {"lo": NS(isup=True), "eth0": NS(isup=True), "wlan0": NS(isup=True), "down0": NS(isup=False)}


class TestInterfaces(unittest.TestCase):
    def setUp(self):
        self.mgr = NetworkInterfaceManager(FakePsutil())

    def test_listing_skips_loopback_and_ipless(self):
        names = [i.name for i in self.mgr.list_interfaces()]
        self.assertEqual(names, ["eth0", "wlan0", "down0"])      # up first, sorted

    def test_interface_details(self):
        eth = self.mgr.get("eth0")
        self.assertEqual(eth.mac, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(str(eth.network), "192.168.1.0/24")

    def test_unavailable_interfaces(self):
        with self.assertRaises(InterfaceError):
            self.mgr.get("nope")
        with self.assertRaises(InterfaceError):
            self.mgr.get("down0")

    def test_psutil_failure(self):
        class Boom:
            def net_if_addrs(self): raise OSError("denied")
        with self.assertRaises(InterfaceError):
            NetworkInterfaceManager(Boom()).list_interfaces()

    def test_real_psutil_smoke(self):
        try:
            NetworkInterfaceManager().list_interfaces()          # must not raise on a real system
        except InterfaceError as exc:
            self.fail(str(exc))


class TestSubnetManager(unittest.TestCase):
    iface = InterfaceInfo("eth0", "192.168.1.10", "255.255.255.0", "aa:bb:cc:dd:ee:ff", True)

    def test_default_scope_is_interface_network(self):
        self.assertEqual(str(SubnetManager.resolve_scope(self.iface, None, 1024)), "192.168.1.0/24")
        self.assertEqual(str(SubnetManager.resolve_scope(self.iface, "  ", 1024)), "192.168.1.0/24")

    def test_narrower_scope_allowed_foreign_rejected(self):
        self.assertEqual(str(SubnetManager.resolve_scope(self.iface, "192.168.1.0/28", 1024)), "192.168.1.0/28")
        for bad in ("192.168.2.0/24", "8.8.8.0/24", "192.168.0.0/16", "garbage"):
            with self.subTest(bad), self.assertRaises(ValidationError):
                SubnetManager.resolve_scope(self.iface, bad, 1024)

    def test_iter_hosts_excludes(self):
        hosts = list(SubnetManager.iter_hosts(ipaddress.ip_network("10.0.0.0/29"), exclude=["10.0.0.2"]))
        self.assertEqual(hosts, ["10.0.0.1", "10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6"])


class TestNetworkUtils(unittest.TestCase):
    def test_locally_administered(self):
        self.assertTrue(is_locally_administered("02:00:00:00:00:01"))
        self.assertTrue(is_locally_administered("da:a1:19:00:00:00"))
        self.assertFalse(is_locally_administered("b8:27:eb:00:00:01"))

    def test_default_gateway_parsing(self):
        content = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                   "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
                   "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n")
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(content)
        try:
            self.assertEqual(get_default_gateway("eth0", fh.name), "192.168.1.1")
            self.assertIsNone(get_default_gateway("wlan0", fh.name))
            self.assertIsNone(get_default_gateway("eth0", "/nonexistent/file"))
        finally:
            os.unlink(fh.name)

    def test_capability_parse(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("Name:\tpython\nCapEff:\t0000000000002000\n")
        try:
            if os.geteuid() != 0:
                self.assertTrue(has_raw_socket_privilege(fh.name))
        finally:
            os.unlink(fh.name)

    def test_probe_ports_finds_local_listener_only(self):
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertEqual(probe_ports("127.0.0.1", [port], 0.5), [port])
            cancel = threading.Event(); cancel.set()
            self.assertEqual(probe_ports("127.0.0.1", [port], 0.5, cancel=cancel), [])
        finally:
            srv.close()
        self.assertEqual(probe_ports("127.0.0.1", [port], 0.5), [])       # closed now

    def test_rate_limiter_spacing(self):
        t = {"now": 0.0}; slept = []
        rl = RateLimiter(10, clock=lambda: t["now"], sleep=lambda s: slept.append(round(s, 3)))
        for _ in range(3):
            rl.acquire()
        self.assertEqual(slept, [0.1, 0.2])
        with self.assertRaises(ValueError):
            RateLimiter(0)


class TestOUI(unittest.TestCase):
    def test_parse_ieee_and_nmap_formats(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as fh:
            fh.write("28-6F-B9   (hex)\t\tNokia Shanghai Bell Co., Ltd.\n286FB9     (base 16)\t\tNokia\n")
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".nmap") as fh2:
            fh2.write("# comment\n001122 Acme Corp\n")
        try:
            self.assertEqual(ManufacturerLookup([fh.name]).lookup("28:6f:b9:01:02:03"), "Nokia Shanghai Bell Co., Ltd.")
            self.assertEqual(ManufacturerLookup([fh2.name]).lookup("00:11:22:33:44:55"), "Acme Corp")
        finally:
            os.unlink(fh.name); os.unlink(fh2.name)

    def test_randomised_mac_has_no_vendor_and_builtin(self):
        lk = ManufacturerLookup([])
        self.assertIsNone(lk.lookup("02:00:00:00:00:01"))
        self.assertEqual(lk.lookup("B8:27:EB:11:22:33"), "Raspberry Pi Foundation")
        self.assertIsNone(lk.lookup("garbage"))


if __name__ == "__main__":
    unittest.main()
