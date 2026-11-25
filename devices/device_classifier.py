"""Evidence-based device classification.

Each signal contributes (category, weight, kind, reason). Within one *kind* of
evidence only the strongest hit per category counts, so repeated matches of the
same signal cannot inflate confidence. Vendor evidence is deliberately capped
below the HIGH threshold: a manufacturer alone never gives certainty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from devices.device import Category as C, Confidence, Device

C_, CF = C, Confidence


@dataclass(frozen=True)
class Evidence:
    category: C
    weight: int
    kind: str
    reason: str


@dataclass(frozen=True)
class Classification:
    category: C
    confidence: Confidence
    reasons: tuple[str, ...]


# (substring of lowercase vendor, category, weight)
VENDOR_RULES: list[tuple[str, C, int]] = [
    *[(v, C.CCTV_CAMERA, 45) for v in ("hikvision", "dahua", "axis communications", "reolink", "amcrest",
                                         "foscam", "lorex", "uniview", "vivotek", "hanwha", "ezviz", "wyze")],
    ("ring", C.CCTV_CAMERA, 20),
    ("tp-link", C.ROUTER, 25), ("netgear", C.ROUTER, 30), ("d-link", C.ROUTER, 25), ("zyxel", C.ROUTER, 35),
    ("mikrotik", C.ROUTER, 45), ("routerboard", C.ROUTER, 45), ("ubiquiti", C.ROUTER, 30), ("cisco", C.ROUTER, 25),
    ("arris", C.ROUTER, 30), ("technicolor", C.ROUTER, 35), ("sagemcom", C.ROUTER, 40), ("aruba", C.ROUTER, 35),
    ("ruckus", C.ROUTER, 35), ("linksys", C.ROUTER, 35), ("huawei", C.ROUTER, 15), ("huawei", C.SMARTPHONE, 20),
    ("apple", C.SMARTPHONE, 25), ("apple", C.COMPUTER, 20), ("samsung", C.SMARTPHONE, 20), ("samsung", C.SMART_TV, 15),
    ("xiaomi", C.SMARTPHONE, 25), ("oneplus", C.SMARTPHONE, 40), ("oppo", C.SMARTPHONE, 40),
    ("vivo mobile", C.SMARTPHONE, 40), ("realme", C.SMARTPHONE, 40), ("motorola", C.SMARTPHONE, 30),
    ("google", C.SMARTPHONE, 10), ("google", C.IOT_DEVICE, 15),
    ("intel corporate", C.COMPUTER, 25), ("dell", C.COMPUTER, 40), ("lenovo", C.COMPUTER, 35),
    ("micro-star", C.COMPUTER, 40), ("gigabyte", C.COMPUTER, 35), ("asustek", C.COMPUTER, 25),
    ("liteon", C.COMPUTER, 20), ("vmware", C.COMPUTER, 40), ("virtualbox", C.COMPUTER, 40),
    ("qemu", C.COMPUTER, 40), ("raspberry pi", C.IOT_DEVICE, 30), ("raspberry pi", C.COMPUTER, 25),
    ("lg electronics", C.SMART_TV, 30), ("vizio", C.SMART_TV, 50), ("tcl", C.SMART_TV, 40),
    ("hisense", C.SMART_TV, 50), ("roku", C.SMART_TV, 55), ("sony", C.SMART_TV, 15), ("panasonic", C.SMART_TV, 15),
    ("epson", C.PRINTER, 40), ("brother", C.PRINTER, 45), ("xerox", C.PRINTER, 50), ("lexmark", C.PRINTER, 55),
    ("ricoh", C.PRINTER, 50), ("kyocera", C.PRINTER, 50), ("konica", C.PRINTER, 50), ("canon", C.PRINTER, 25),
    ("hp inc", C.PRINTER, 15), ("hewlett packard", C.PRINTER, 15), ("hewlett packard", C.COMPUTER, 15),
    ("synology", C.NETWORK_STORAGE, 60), ("qnap", C.NETWORK_STORAGE, 60), ("asustor", C.NETWORK_STORAGE, 55),
    ("western digital", C.NETWORK_STORAGE, 30), ("buffalo", C.NETWORK_STORAGE, 30), ("drobo", C.NETWORK_STORAGE, 55),
    ("espressif", C.IOT_DEVICE, 45), ("tuya", C.IOT_DEVICE, 50), ("allterco", C.IOT_DEVICE, 50),
    ("shelly", C.IOT_DEVICE, 50), ("itead", C.IOT_DEVICE, 50), ("amazon technologies", C.IOT_DEVICE, 30),
    ("sonos", C.IOT_DEVICE, 40), ("signify", C.IOT_DEVICE, 50), ("philips lighting", C.IOT_DEVICE, 50),
    ("nest labs", C.IOT_DEVICE, 35), ("belkin", C.IOT_DEVICE, 30), ("ecobee", C.IOT_DEVICE, 50),
    ("yeelight", C.IOT_DEVICE, 50), ("texas instruments", C.IOT_DEVICE, 15), ("murata", C.IOT_DEVICE, 15),
    ("fitbit", C.SMARTWATCH, 55), ("garmin", C.SMARTWATCH, 50), ("huami", C.SMARTWATCH, 55), ("amazfit", C.SMARTWATCH, 55),
]

# (regex on lowercase text, category, weight, label)
NAME_RULES: list[tuple[re.Pattern, C, int, str]] = [(re.compile(p), c, w, l) for p, c, w, l in [
    (r"iphone|ipad|galaxy[-_ ]?s\d|galaxy[-_ ]?a\d|android|pixel-?\d|redmi|oneplus|xiaomi-|huawei-", C.SMARTPHONE, 55, "phone-style name"),
    (r"apple[-_ ]?watch|galaxy[-_ ]?watch|fitbit|garmin|amazfit|mi[-_ ]?band|smart[-_ ]?watch", C.SMARTWATCH, 55, "watch-style name"),
    (r"laptop|desktop|macbook|imac|thinkpad|workstation|(^|[-_.])pc($|[-_.\d])|^pc-", C.COMPUTER, 50, "computer-style name"),
    (r"(^|[-_.])tv($|[-_.\d])|bravia|roku|firetv|fire-tv|chromecast|appletv|apple-tv|webos|tizen", C.SMART_TV, 50, "TV-style name"),
    (r"(^|[-_.])(ip)?cam(era)?($|[-_.\d])|ipcam|(^|[-_.])[nd]vr($|[-_.\d])|hikvision|dahua|(^|[-_.])ipc($|[-_.\d])", C.CCTV_CAMERA, 55, "camera-style name"),
    (r"printer|laserjet|officejet|deskjet|mfc-|(^|[-_.])npi[0-9a-f]{6}|hp[0-9a-f]{6}", C.PRINTER, 55, "printer-style name"),
    (r"router|gateway|openwrt|access-?point|(^|[-_.])ap($|[-_.\d])|modem|mikrotik|ubnt|unifi", C.ROUTER, 45, "router-style name"),
    (r"(^|[-_.])nas($|[-_.\d])|synology|diskstation|qnap|readynas", C.NETWORK_STORAGE, 55, "storage-style name"),
    (r"esp(32|8266)|esp[-_]?[0-9a-f]{4,}|shelly|tasmota|sonoff|tuya|echo|alexa|homepod|nest|(^|[-_.])hue|bulb|smart-?plug|sensor", C.IOT_DEVICE, 45, "IoT-style name"),
]]

# mDNS service-type substring -> (category, weight)
MDNS_RULES: list[tuple[str, C, int]] = [
    ("_ipp.", C.PRINTER, 85), ("_ipps.", C.PRINTER, 85), ("_printer.", C.PRINTER, 85),
    ("_pdl-datastream.", C.PRINTER, 85), ("_scanner.", C.PRINTER, 70),
    ("_airplay.", C.SMART_TV, 35), ("_googlecast.", C.SMART_TV, 55), ("_androidtvremote", C.SMART_TV, 80),
    ("_mediaremotetv.", C.SMART_TV, 65), ("_hap.", C.IOT_DEVICE, 55), ("_homekit.", C.IOT_DEVICE, 55),
    ("_companion-link.", C.SMARTPHONE, 40), ("_apple-mobdev2.", C.SMARTPHONE, 65),
    ("_rtsp.", C.CCTV_CAMERA, 60), ("_onvif.", C.CCTV_CAMERA, 85), ("_axis-video.", C.CCTV_CAMERA, 85),
    ("_smb.", C.COMPUTER, 25), ("_smb.", C.NETWORK_STORAGE, 25), ("_afpovertcp.", C.NETWORK_STORAGE, 30),
    ("_afpovertcp.", C.COMPUTER, 25), ("_adisk.", C.NETWORK_STORAGE, 55), ("_workstation.", C.COMPUTER, 45),
    ("_ssh.", C.COMPUTER, 15), ("_esphomelib.", C.IOT_DEVICE, 85), ("_hue.", C.IOT_DEVICE, 70),
    ("_matter.", C.IOT_DEVICE, 50), ("_matterc.", C.IOT_DEVICE, 50), ("_sonos.", C.IOT_DEVICE, 60),
    ("_spotify-connect.", C.IOT_DEVICE, 20),
]

SSDP_RULES: list[tuple[re.Pattern, C, int, str]] = [(re.compile(p, re.I), c, w, l) for p, c, w, l in [
    (r"internetgatewaydevice|wanconnectiondevice|wanipconnection", C.ROUTER, 85, "UPnP Internet Gateway Device"),
    (r"wlanaccesspoint|wfadevice", C.ROUTER, 60, "UPnP access point"),
    (r"miniupnpd", C.ROUTER, 40, "MiniUPnPd server banner (common on routers)"),
    (r"mediarenderer", C.SMART_TV, 40, "UPnP media renderer"),
    (r"dial-multiscreen|roku:ecp|smarttv|tizen|webos|samsung.*tv", C.SMART_TV, 70, "smart-TV UPnP identifiers"),
    (r"device:printer|printbasic", C.PRINTER, 85, "UPnP printer"),
    (r"digitalsecuritycamera|networkcamera|ipcamera", C.CCTV_CAMERA, 85, "UPnP camera"),
    (r"synology|qnap|readynas|networkstorage", C.NETWORK_STORAGE, 55, "storage UPnP identifiers"),
    (r"sonos|zoneplayer|hue-bridgeid", C.IOT_DEVICE, 65, "smart-home UPnP identifiers"),
    (r"microsoft-windows|windows/", C.COMPUTER, 45, "Windows UPnP banner"),
]]

# open TCP port -> (category, weight, label)
PORT_RULES: dict[int, list[tuple[C, int, str]]] = {
    554: [(C.CCTV_CAMERA, 50, "RTSP (554) open")], 8554: [(C.CCTV_CAMERA, 40, "RTSP (8554) open")],
    37777: [(C.CCTV_CAMERA, 70, "Dahua SDK port 37777 open")],
    9100: [(C.PRINTER, 60, "JetDirect (9100) open")], 515: [(C.PRINTER, 50, "LPD (515) open")],
    631: [(C.PRINTER, 60, "IPP (631) open")],
    445: [(C.COMPUTER, 20, "SMB (445) open"), (C.NETWORK_STORAGE, 15, "SMB (445) open")],
    139: [(C.COMPUTER, 15, "NetBIOS (139) open")],
    5000: [(C.NETWORK_STORAGE, 25, "port 5000 open (DSM-style)")], 548: [(C.NETWORK_STORAGE, 25, "AFP (548) open")],
    3389: [(C.COMPUTER, 55, "RDP (3389) open")], 53: [(C.ROUTER, 30, "DNS (53) open")],
    7547: [(C.ROUTER, 40, "TR-069 (7547) open")],
    8008: [(C.SMART_TV, 50, "Cast HTTP (8008) open")], 8009: [(C.SMART_TV, 50, "Cast (8009) open")],
    62078: [(C.SMARTPHONE, 50, "iOS lockdown service (62078) open")],
}


def _vendor_evidence(text: str, kind: str, label: str) -> list[Evidence]:
    t = text.lower()
    return [Evidence(c, w, kind, f"{label} '{text}' is typical for {c.value.lower()}")
            for sub, c, w in VENDOR_RULES if sub in t]


def _name_evidence(text: str, kind: str, label: str) -> list[Evidence]:
    t = text.lower()
    return [Evidence(c, w, kind, f"{label} '{text}': {why}") for rx, c, w, why in NAME_RULES if rx.search(t)]


class DeviceClassifier:
    HIGH_SCORE = 120
    MEDIUM_SCORE = 50
    LOW_SCORE = 20
    STRONG_WEIGHT = 85

    def gather(self, d: Device) -> list[Evidence]:
        ev: list[Evidence] = []
        if d.is_self:
            ev.append(Evidence(C.COMPUTER, 90, "self", "this is the machine running NETWORK RADAR"))
        if d.is_gateway:
            ev.append(Evidence(C.ROUTER, 85, "gateway", "default gateway of the scanned network"))
        if d.ssid is not None or "wifi" in d.sources:
            if d.ip:
                ev.append(Evidence(C.ROUTER, 60, "beacon", "also broadcasts a Wi-Fi beacon (access point)"))
            else:
                ev.append(Evidence(C.WIFI_AP, 85, "beacon", "broadcasts a Wi-Fi beacon (access point)"))
        if d.transport == "bluetooth" or "bluetooth" in d.sources:
            ev.append(Evidence(C.BLUETOOTH, 25, "radio", "seen through Bluetooth advertising"))
        if d.vendor:
            ev += _vendor_evidence(d.vendor, "vendor", "manufacturer")
        if d.randomized_mac:
            ev.append(Evidence(C.SMARTPHONE, 20, "mac",
                               "randomised (locally administered) MAC - common on phones/watches, also on VMs"))
        if d.hostname:
            ev += _name_evidence(d.hostname, "hostname", "hostname")
        if d.bt_name:
            ev += _name_evidence(d.bt_name, "bluetooth", "Bluetooth name")
        for svc in d.mdns_services:
            s = svc.lower() + "."
            for sub, c, w in MDNS_RULES:
                if sub in s:
                    ev.append(Evidence(c, w, "mdns", f"advertises mDNS service {svc}"))
        if d.ssdp_info:
            blob = " ".join(d.ssdp_info.get(k, "") for k in ("st", "usn", "deviceType", "server"))
            for rx, c, w, why in SSDP_RULES:
                if rx.search(blob):
                    ev.append(Evidence(c, w, "ssdp", f"SSDP: {why}"))
            if d.ssdp_info.get("manufacturer"):
                ev += _vendor_evidence(d.ssdp_info["manufacturer"], "ssdp", "SSDP manufacturer")
            for k in ("friendlyName", "modelName"):
                if d.ssdp_info.get(k):
                    ev += _name_evidence(d.ssdp_info[k], "ssdp", f"SSDP {k}")
        for port in d.open_ports:
            for c, w, why in PORT_RULES.get(port, ()):
                ev.append(Evidence(c, w, "service", why))
        return ev

    def classify(self, d: Device) -> Classification:
        best: dict[tuple[C, str], Evidence] = {}
        for e in self.gather(d):
            cur = best.get((e.category, e.kind))
            if cur is None or e.weight > cur.weight:
                best[(e.category, e.kind)] = e
        scores: dict[C, int] = {}
        for (cat, _kind), e in best.items():
            scores[cat] = scores.get(cat, 0) + e.weight
        if not scores:
            return Classification(C.UNKNOWN, Confidence.UNKNOWN, ("no identifying evidence yet",))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        if top_score < self.LOW_SCORE:
            return Classification(C.UNKNOWN, Confidence.UNKNOWN,
                                  ("evidence too weak: " + "; ".join(self._reasons(best, top)),))
        top_ev = [e for (c, _k), e in best.items() if c is top]
        kinds = {e.kind for e in top_ev}
        if (top_score >= self.HIGH_SCORE and len(kinds) >= 2) or max(e.weight for e in top_ev) >= self.STRONG_WEIGHT:
            level = Confidence.HIGH
        elif top_score >= self.MEDIUM_SCORE:
            level = Confidence.MEDIUM
        else:
            level = Confidence.LOW
        if kinds <= {"vendor", "mac"} and level is Confidence.HIGH:   # manufacturer alone is never certain
            level = Confidence.MEDIUM
        if second >= 0.7 * top_score and level is not Confidence.LOW:  # ambiguous -> demote one level
            level = Confidence.MEDIUM if level is Confidence.HIGH else Confidence.LOW
        return Classification(top, level, tuple(self._reasons(best, top)))

    @staticmethod
    def _reasons(best: dict[tuple[C, str], Evidence], cat: C) -> list[str]:
        items = sorted((e for (c, _k), e in best.items() if c is cat), key=lambda e: -e.weight)
        return [e.reason for e in items]
