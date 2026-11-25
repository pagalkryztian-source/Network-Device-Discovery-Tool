import ipaddress
import struct
import threading
import time
import unittest
from types import SimpleNamespace as NS

from config.settings import Settings
from core.event_manager import EventBus, EventType
from core.network_monitor import NetworkMonitor
from devices.device import Status
from devices.device_registry import DeviceRegistry
from devices.manufacturer_lookup import ManufacturerLookup
from devices.observation import Observation
from discovery.arp_discovery import ARPDiscovery, parse_arp_responses
from discovery.base_discovery import BaseDiscovery, DiscoveryContext, DiscoveryError
from discovery.bluetooth_discovery import parse_bluetoothctl
from discovery.mdns_discovery import (META_QUERY, build_query, encode_name, extract_info, parse_message)
from discovery.ssdp_discovery import parse_description, parse_ssdp_response
from network.interface_manager import InterfaceInfo

NET = ipaddress.ip_network("192.168.1.0/29")           # hosts .1-.6
IFACE = InterfaceInfo("eth0", "192.168.1.1", "255.255.255.248", "02:11:22:33:44:55", True)
CTX = DiscoveryContext(IFACE, NET, packet_rate=1000, batch_size=2, concurrency=2, gateway="192.168.1.2")


def reply(ip, mac):
    return NS(psrc=ip, hwsrc=mac)


class FakeScapy:
    """Stands in for scapy.all: records every packet 'sent' and answers for configured hosts."""
    def __init__(self, alive, raises=None):
        self.alive, self.raises, self.sent, self.calls = alive, raises, [], 0

    def Ether(self, dst): return _Pkt()
    def ARP(self, pdst): return _Pkt(pdst=pdst)

    def srp(self, packets, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        ips = [p.pdst for p in packets]
        self.sent += ips
        return [(None, reply(ip, self.alive[ip])) for ip in ips if ip in self.alive], []


class _Pkt:
    def __init__(self, pdst=None): self.pdst = pdst
    def __truediv__(self, other): return other


class TestARP(unittest.TestCase):
    def test_parse_filters_scope_and_bad_macs_and_dedupes(self):
        pairs = [(None, reply("192.168.1.3", "AA:BB:CC:00:00:03")),
                 (None, reply("192.168.1.3", "aa:bb:cc:00:00:03")),      # duplicate
                 (None, reply("10.9.9.9", "aa:bb:cc:00:00:99")),         # outside scope
                 (None, reply("192.168.1.4", "00:00:00:00:00:00")),      # bad MAC
                 (None, reply("192.168.1.5", None))]
        obs = parse_arp_responses(pairs, NET)
        self.assertEqual([(o.ip, o.mac) for o in obs], [("192.168.1.3", "aa:bb:cc:00:00:03")])

    def test_discovery_only_targets_scope_and_batches(self):
        api = FakeScapy({"192.168.1.2": "aa:aa:aa:aa:aa:02", "192.168.1.5": "aa:aa:aa:aa:aa:05"})
        obs = ARPDiscovery(api).discover(CTX, threading.Event())
        self.assertEqual(sorted(api.sent), ["192.168.1.2", "192.168.1.3", "192.168.1.4", "192.168.1.5", "192.168.1.6"])
        self.assertEqual(api.calls, 3)                                   # 5 hosts, batch of 2
        by_ip = {o.ip: o for o in obs}
        self.assertTrue(by_ip["192.168.1.1"].is_self)                    # local host reported from interface data
        self.assertTrue(by_ip["192.168.1.2"].is_gateway)
        self.assertFalse(by_ip["192.168.1.5"].is_gateway)

    def test_cancellation_stops_between_batches(self):
        api = FakeScapy({})
        cancel = threading.Event()
        orig = api.srp

        def srp(packets, **kw):
            cancel.set()
            return orig(packets, **kw)
        api.srp = srp
        ARPDiscovery(api).discover(CTX, cancel)
        self.assertEqual(api.calls, 1)

    def test_permission_error_is_fatal_with_helpful_message(self):
        with self.assertRaises(DiscoveryError) as cm:
            ARPDiscovery(FakeScapy({}, raises=PermissionError())).discover(CTX, threading.Event())
        self.assertTrue(cm.exception.fatal)
        self.assertIn("CAP_NET_RAW", str(cm.exception))

    def test_missing_interface_is_fatal(self):
        with self.assertRaises(DiscoveryError) as cm:
            ARPDiscovery(FakeScapy({}, raises=OSError(19, "No such device"))).discover(CTX, threading.Event())
        self.assertTrue(cm.exception.fatal)
        self.assertIn("eth0", str(cm.exception))


def dns_name(name): return encode_name(name)


def build_response():
    """Hand-built mDNS response: PTR _ipp._tcp.local -> 'Office._ipp._tcp.local', SRV, A, with compression."""
    hdr = struct.pack("!HHHHHH", 0, 0x8400, 0, 3, 0, 0)
    inst = dns_name("Office._ipp._tcp.local")
    ptr = dns_name("_ipp._tcp.local") + struct.pack("!HHIH", 12, 1, 120, len(inst)) + inst
    target = dns_name("office-printer.local")
    srv_rdata = struct.pack("!HHH", 0, 0, 631) + target
    srv = inst + struct.pack("!HHIH", 33, 1, 120, len(srv_rdata)) + srv_rdata
    a = target + struct.pack("!HHIH", 1, 1, 120, 4) + bytes([192, 168, 1, 4])
    return hdr + ptr + srv + a


class TestMDNS(unittest.TestCase):
    def test_query_roundtrip_structure(self):
        q = build_query([META_QUERY, "_ipp._tcp"])
        qd = struct.unpack("!H", q[4:6])[0]
        self.assertEqual(qd, 2)

    def test_parse_response(self):
        host, services, listed = extract_info(parse_message(build_response()))
        self.assertEqual(host, "office-printer")
        self.assertEqual(services, {"_ipp._tcp"})
        self.assertEqual(listed, set())

    def test_meta_query_response_lists_types(self):
        t = dns_name("_airplay._tcp.local")
        hdr = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        rec = dns_name(META_QUERY) + struct.pack("!HHIH", 12, 1, 120, len(t)) + t
        _, services, listed = extract_info(parse_message(hdr + rec))
        self.assertEqual(listed, {"_airplay._tcp"})

    def test_malformed_and_hostile_packets_are_ignored(self):
        self.assertEqual(parse_message(b""), [])
        self.assertEqual(parse_message(b"\x00" * 5), [])
        loop = struct.pack("!HHHHHH", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c" + b"\x00" * 10   # pointer to itself
        self.assertEqual(parse_message(loop), [])
        self.assertEqual(parse_message(build_response()[:-3]), parse_message(build_response()[:-3]))


class TestSSDP(unittest.TestCase):
    def test_parse_response(self):
        raw = (b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLOCATION: http://192.168.1.2:49152/desc.xml\r\n"
               b"SERVER: Linux/4.4 UPnP/1.0 MiniUPnPd/2.1\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
               b"USN: uuid:abc::urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n")
        h = parse_ssdp_response(raw)
        self.assertEqual(h["location"], "http://192.168.1.2:49152/desc.xml")
        self.assertIn("InternetGatewayDevice", h["st"])
        self.assertEqual(parse_ssdp_response(b"garbage"), {})
        self.assertEqual(parse_ssdp_response(b"HTTP/1.1 404 Not Found\r\n\r\n"), {})

    def test_parse_description(self):
        xml = (b'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
               b"<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>"
               b"<friendlyName>Living Room TV</friendlyName><manufacturer>Acme</manufacturer>"
               b"<modelName>X1</modelName></device></root>")
        d = parse_description(xml)
        self.assertEqual(d["friendlyName"], "Living Room TV")
        self.assertEqual(d["manufacturer"], "Acme")

    def test_entity_expansion_rejected(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><root><friendlyName>&a;</friendlyName></root>'
        self.assertEqual(parse_description(evil), {})
        self.assertEqual(parse_description(b"<not xml"), {})


class TestBluetoothParse(unittest.TestCase):
    def test_parse(self):
        out = ("\x1b[0;92m[NEW]\x1b[0m Device AA:BB:CC:11:22:33 Fitbit Charge 5\n"
               "[NEW] Device 11:22:33:44:55:66 11-22-33-44-55-66\n"
               "[CHG] Device 11:22:33:44:55:66 RSSI: -70\n"
               "[CHG] Device 11:22:33:44:55:66 Name: Galaxy Buds\n"
               "unrelated line\n")
        devs = parse_bluetoothctl(out)
        self.assertEqual(devs["aa:bb:cc:11:22:33"], "Fitbit Charge 5")
        self.assertEqual(devs["11:22:33:44:55:66"], "Galaxy Buds")


# ------------------------------------------------------------------ monitor
class ScriptedDiscovery(BaseDiscovery):
    def __init__(self, name, script):
        self.name, self.script, self.calls = name, list(script), 0

    def discover(self, ctx, cancel):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


def obs(ip, mac, src="arp"): return Observation(source=src, ip=ip, mac=mac)


def build_monitor(discoverers, settings=None, **kw):
    s = settings or Settings(missed_threshold=2, enable_hostname=False, enable_probe=False)
    bus = EventBus()
    reg = DeviceRegistry(ManufacturerLookup([]), missed_threshold=s.missed_threshold)
    mon = NetworkMonitor(reg, bus, discoverers, s, **kw)
    mon._ctx = CTX
    return mon, reg, bus


def types(bus): return [e.type for e in bus.drain()]


class TestMonitor(unittest.TestCase):
    def test_discover_offline_reappear_events(self):
        a, b = obs("192.168.1.3", "aa:aa:aa:aa:aa:03"), obs("192.168.1.4", "aa:aa:aa:aa:aa:04")
        arp = ScriptedDiscovery("arp", [[a, b], [a], [a], [a, b]])
        mon, reg, bus = build_monitor([arp])
        mon.run_cycle()
        ev = types(bus)
        self.assertEqual(ev.count(EventType.DEVICE_DISCOVERED), 2)
        self.assertIn(EventType.CYCLE_COMPLETED, ev)
        mon.run_cycle(); self.assertEqual(types(bus).count(EventType.DEVICE_OFFLINE), 0)    # missed 1 < 2
        mon.run_cycle(); self.assertEqual(types(bus).count(EventType.DEVICE_OFFLINE), 1)    # missed 2
        self.assertIs(reg.get("aa:aa:aa:aa:aa:04").status, Status.OFFLINE)
        mon.run_cycle(); self.assertEqual(types(bus).count(EventType.DEVICE_REAPPEARED), 1)
        self.assertIs(reg.get("aa:aa:aa:aa:aa:04").status, Status.ONLINE)

    def test_out_of_scope_observations_are_dropped(self):
        arp = ScriptedDiscovery("arp", [[obs("192.168.1.3", "aa:aa:aa:aa:aa:03"), obs("10.1.1.1", "aa:aa:aa:aa:aa:99")]])
        mon, reg, _ = build_monitor([arp])
        mon.run_cycle()
        self.assertEqual(reg.total_count, 1)

    def test_discovery_failure_reports_error_and_keeps_devices_online(self):
        arp = ScriptedDiscovery("arp", [[obs("192.168.1.3", "aa:aa:aa:aa:aa:03")], DiscoveryError("boom")])
        mon, reg, bus = build_monitor([arp], Settings(missed_threshold=1, enable_hostname=False, enable_probe=False))
        mon.run_cycle(); bus.drain()
        mon.run_cycle()
        errs = [e for e in bus.drain() if e.type is EventType.SCAN_ERROR]
        self.assertEqual(len(errs), 1)
        self.assertIn("boom", errs[0].payload["message"])
        self.assertEqual(reg.online_count, 1)                      # failure must not mark anything offline

    def test_unexpected_exception_is_reported_not_swallowed(self):
        arp = ScriptedDiscovery("arp", [RuntimeError("kaput")])
        mon, _, bus = build_monitor([arp])
        with self.assertLogs("core.network_monitor", level="ERROR"):
            mon.run_cycle()
        self.assertTrue(any(e.type is EventType.SCAN_ERROR and "kaput" in e.payload["message"] for e in bus.drain()))

    def test_fatal_error_stops_loop(self):
        arp = ScriptedDiscovery("arp", [DiscoveryError("no permission", fatal=True)])
        mon, _, bus = build_monitor([arp])
        mon.settings.update(scan_interval=5)
        mon.start(IFACE, NET)
        mon._thread.join(5)
        ev = types(bus)
        self.assertFalse(mon.is_running)
        self.assertEqual(ev[0], EventType.SCAN_STARTED)
        self.assertEqual(ev[-1], EventType.SCAN_STOPPED)

    def test_disabled_discoverer_not_called(self):
        arp = ScriptedDiscovery("arp", [[]]); mdns = ScriptedDiscovery("mdns", [[]])
        mon, _, _ = build_monitor([arp, mdns], Settings(enable_mdns=False, enable_hostname=False, enable_probe=False))
        mon.run_cycle()
        self.assertEqual((arp.calls, mdns.calls), (1, 0))

    def test_overlapping_cycles_prevented(self):
        gate, entered = threading.Event(), threading.Event()

        class Slow(BaseDiscovery):
            name = "arp"
            def discover(self, ctx, cancel):
                entered.set(); gate.wait(5); return []

        mon, _, _ = build_monitor([Slow()])
        t = threading.Thread(target=mon.run_cycle); t.start()
        self.assertTrue(entered.wait(5))
        with self.assertLogs("core.network_monitor", level="WARNING"):
            self.assertFalse(mon.run_cycle())                       # second cycle refused while first runs
        gate.set(); t.join(5)
        self.assertTrue(mon.run_cycle())

    def test_graceful_cancellation(self):
        started = threading.Event()

        class Blocking(BaseDiscovery):
            name = "arp"
            def discover(self, ctx, cancel):
                started.set(); cancel.wait(10); return []

        mon, _, bus = build_monitor([Blocking()])
        mon.start(IFACE, NET)
        self.assertTrue(started.wait(5))
        t0 = time.time(); mon.stop(5)
        self.assertLess(time.time() - t0, 3)
        self.assertFalse(mon.is_running)
        self.assertEqual(types(bus)[-1], EventType.SCAN_STOPPED)

    def test_enrichment_uses_injected_helpers_and_classifies(self):
        arp = ScriptedDiscovery("arp", [[obs("192.168.1.3", "aa:aa:aa:aa:aa:03")]])
        mon, reg, bus = build_monitor(
            [arp], Settings(enable_hostname=True, enable_probe=True, concurrency=2),
            hostname_resolver=lambda ip: "ipcam-front", port_prober=lambda ip, ports, t, lim, c: [554])
        mon.run_cycle()
        d = reg.get("aa:aa:aa:aa:aa:03")
        self.assertEqual(d.hostname, "ipcam-front")
        self.assertEqual(d.open_ports, {554})
        self.assertEqual(d.effective_category.name, "CCTV_CAMERA")
        self.assertIn(EventType.CLASSIFICATION_UPDATED, types(bus))
        # a second cycle must not re-probe (refresh interval not elapsed)
        calls = []
        mon._probe = lambda *a: calls.append(a) or []
        mon.run_cycle()
        self.assertEqual(calls, [])

    def test_start_twice_rejected(self):
        mon, _, _ = build_monitor([ScriptedDiscovery("arp", [[]])])
        mon.start(IFACE, NET)
        try:
            with self.assertRaises(RuntimeError):
                mon.start(IFACE, NET)
        finally:
            mon.stop(5)


class TestEventBus(unittest.TestCase):
    def test_drops_oldest_when_full(self):
        bus = EventBus(maxsize=3)
        for i in range(5):
            bus.publish(EventType.DEVICE_UPDATED, n=i)
        self.assertEqual([e.payload["n"] for e in bus.drain()], [2, 3, 4])

    def test_dispatch_and_failing_subscriber(self):
        bus, got = EventBus(), []
        bus.subscribe(EventType.SCAN_STARTED, lambda e: 1 / 0)
        bus.subscribe(EventType.SCAN_STARTED, got.append)
        bus.publish(EventType.SCAN_STARTED)
        with self.assertLogs("core.event_manager", level="ERROR"):
            self.assertEqual(bus.dispatch(), 1)
        self.assertEqual(len(got), 1)

    def test_concurrent_publish(self):
        bus = EventBus()
        ts = [threading.Thread(target=lambda: [bus.publish(EventType.DEVICE_UPDATED) for _ in range(500)]) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(bus.drain(10000)), 2000)


if __name__ == "__main__":
    unittest.main()
