"""Bluetooth mesh-lite emergency relay (best effort, no internet/cellular/network needed).

WHAT THIS ACTUALLY IS
----------------------
Phones and laptops broadcast small, unconnected advertising packets over Bluetooth Low
Energy constantly (that is how the rest of this app's radar sees them). BLE lets you put a
tiny amount of custom data ("manufacturer data") into that broadcast. This module uses that
to send a short message - a status ping, an "I need help" flag, or free text up to ~20 bytes -
and has every other copy of this app that hears it rebroadcast it again for a few hops
(flood relay with a hop counter and a seen-message cache to stop loops). That is the same
basic idea real disaster-mesh / off-grid chat tools use (e.g. Bridgefy, FireChat, goTenna's
BLE mode): no single device needs to reach far, but a message can hop phone-to-phone across
a crowd, a campsite or a disaster area with the cellular network down - which is exactly the
scenario this app was asked to help with.

HONEST LIMITS - READ BEFORE RELYING ON THIS IN A REAL EMERGENCY
-----------------------------------------------------------------
- Range per hop is ordinary BLE range: tens of metres, less indoors/through walls.
- It ONLY relays between devices that are *also running this software* (or anything else
  listening for the same company ID and decoding the same format). It does not reach
  someone's phone just because they turned Bluetooth on - they need this app running too.
- It needs an adapter that supports BLE advertising with custom manufacturer data via
  BlueZ's `bluetoothctl advertise` menu (BlueZ 5.47+). Older BlueZ, or adapters whose
  firmware limits advertising, may silently fail to transmit - always verify with a second
  device before depending on this.
- Messages are NOT encrypted or authenticated. Anyone nearby running compatible software can
  read and forge them. Do not put sensitive information in a mesh message.
- Company ID 0xFFFF is reserved by the Bluetooth SIG for development/testing only - it is
  used here deliberately so this never collides with a real product's advertising, but it
  also means some OS-level BLE stacks may filter or deprioritise it.
- This was not exercised on real Bluetooth hardware (the build sandbox has no radio); the
  encode/decode/parsing logic is unit-tested against the documented BlueZ output format, but
  you should verify the actual broadcast/relay path on your own two devices before trusting it.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from config.defaults import MESH_COMPANY_ID, MESH_DEFAULT_TTL, MESH_MAX_PAYLOAD_BYTES, MESH_SEEN_CACHE
from utils.validators import normalize_mac

log = logging.getLogger(__name__)

_HEADER_LEN = 6        # msg_id(4) + type(1) + ttl(1), payload follows


class MeshMessageType(IntEnum):
    PING = 0           # "I am here" / heartbeat, no payload needed
    STATUS = 1         # short free-text status
    SOS = 2            # emergency flag + optional short free-text
    TEXT = 3           # short free-text message


@dataclass(frozen=True)
class MeshMessage:
    msg_id: bytes              # 4 bytes, identifies this message for dedup/relay (not a device identity)
    msg_type: MeshMessageType
    ttl: int                   # hops remaining; a relay forwards with ttl-1, never increases it
    text: str = ""              # truncated to fit MESH_MAX_PAYLOAD_BYTES when encoded

    def __post_init__(self) -> None:
        if len(self.msg_id) != 4:
            raise ValueError("msg_id must be exactly 4 bytes")
        if not 0 <= self.ttl <= 255:
            raise ValueError("ttl must fit in one byte (0-255)")


def new_message_id() -> bytes:
    """4 random bytes. Collisions are harmless (worst case: one message dropped early)."""
    import os
    return os.urandom(4)


def encode_message(msg: MeshMessage) -> bytes:
    """Message -> manufacturer-data payload bytes (NOT including the company id itself)."""
    text_bytes = msg.text.encode("utf-8", "replace")[: max(0, MESH_MAX_PAYLOAD_BYTES - _HEADER_LEN)]
    return msg.msg_id + bytes([int(msg.msg_type) & 0xFF, msg.ttl & 0xFF]) + text_bytes


def decode_message(data: bytes) -> Optional[MeshMessage]:
    """manufacturer-data payload bytes -> Message, or None if it is not one of ours / malformed."""
    if len(data) < _HEADER_LEN:
        return None
    msg_id, raw_type, ttl = data[0:4], data[4], data[5]
    try:
        msg_type = MeshMessageType(raw_type)
    except ValueError:
        return None                      # not our message format - ignore quietly, never crash the scan
    text = data[_HEADER_LEN:].decode("utf-8", "replace")
    try:
        return MeshMessage(msg_id=msg_id, msg_type=msg_type, ttl=ttl, text=text)
    except ValueError:
        return None


def relay(msg: MeshMessage) -> Optional[MeshMessage]:
    """The message a relay node should rebroadcast, or None if its TTL is exhausted (do not forward)."""
    if msg.ttl <= 0:
        return None
    return MeshMessage(msg_id=msg.msg_id, msg_type=msg.msg_type, ttl=msg.ttl - 1, text=msg.text)


class SeenCache:
    """Bounded set of recently relayed message ids, so a flood relay does not loop forever."""

    def __init__(self, capacity: int = MESH_SEEN_CACHE) -> None:
        self._capacity = capacity
        self._seen: "OrderedDict[bytes, None]" = OrderedDict()

    def seen_before(self, msg_id: bytes) -> bool:
        if msg_id in self._seen:
            self._seen.move_to_end(msg_id)
            return True
        self._seen[msg_id] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False


# ---------------------------------------------------------------------- bluetoothctl output parsing
_DEVICE_HDR = re.compile(r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")
_MFG_KEY = re.compile(r"ManufacturerData Key:\s*0x([0-9a-fA-F]{1,4})")
_HEX_LINE = re.compile(r"^(?:[0-9a-fA-F]{2}\s+)+[0-9a-fA-F]{2}\b")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


def parse_manufacturer_data(output: str, company_id: int = MESH_COMPANY_ID) -> dict[str, bytes]:
    """Best-effort parse of `bluetoothctl` scan output for one company id's manufacturer data.

    BlueZ prints a device header line, then (on a later/CHG line) the company id, then the
    raw bytes on their own indented line(s). This walks the stream keeping track of "which
    device/company id are we currently inside" rather than assuming a fixed line order, since
    bluetoothctl versions differ in exact formatting.
    """
    result: dict[str, bytes] = {}
    current_mac: Optional[str] = None
    current_company: Optional[int] = None
    for raw in output.splitlines():
        line = _ANSI.sub("", raw).strip()
        dev_m = _DEVICE_HDR.search(line)
        if dev_m:
            mac = normalize_mac(dev_m.group(1))
            if mac:
                current_mac = mac
        key_m = _MFG_KEY.search(line)
        if key_m:
            current_company = int(key_m.group(1), 16)
            continue
        if current_mac and current_company == company_id and _HEX_LINE.match(line):
            hex_bytes = re.findall(r"[0-9a-fA-F]{2}", line)
            try:
                data = bytes(int(h, 16) for h in hex_bytes)
            except ValueError:
                continue
            if data:
                result[current_mac] = result.get(current_mac, b"") + data
            current_company = None       # one hex block consumed; wait for the next Key: line
    return result


class MeshRelay:
    """Best-effort BLE mesh-lite broadcaster/listener built on `bluetoothctl` (see module docstring
    for what this can and cannot do). Safe to construct even with no adapter; calls fail softly
    and log a warning rather than raising into the GUI thread.
    """

    def __init__(self, company_id: int = MESH_COMPANY_ID, default_ttl: int = MESH_DEFAULT_TTL) -> None:
        self.company_id = company_id
        self.default_ttl = default_ttl
        self._seen = SeenCache()

    @staticmethod
    def is_available() -> tuple[bool, str]:
        if shutil.which("bluetoothctl") is None:
            return False, "bluetoothctl not found (sudo apt install bluez) or no Bluetooth adapter."
        return True, ""

    def send(self, msg_type: MeshMessageType, text: str = "", ttl: Optional[int] = None,
              advertise_s: float = 4.0) -> MeshMessage:
        """Broadcast one message for `advertise_s` seconds. Returns the message sent (for logging
        / to show the user what went out) regardless of whether the adapter actually transmitted -
        verify with a second device, see module docstring."""
        msg = MeshMessage(msg_id=new_message_id(), msg_type=msg_type, ttl=self.default_ttl if ttl is None else ttl,
                          text=text)
        self._broadcast(msg, advertise_s)
        return msg

    def _broadcast(self, msg: MeshMessage, advertise_s: float) -> None:
        ok, why = self.is_available()
        if not ok:
            log.warning("Mesh broadcast skipped: %s", why)
            return
        payload_hex = encode_message(msg).hex()
        script = (
            "menu advertise\n"
            f"manufacturer 0x{self.company_id:04x} {' '.join(payload_hex[i:i+2] for i in range(0, len(payload_hex), 2))}\n"
            "back\n"
            "advertise on\n"
        )
        try:
            proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            proc.stdin.write(script)
            proc.stdin.flush()
            time.sleep(max(0.5, advertise_s))
            proc.stdin.write("advertise off\nexit\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:
            log.exception("Mesh broadcast via bluetoothctl failed (adapter/BlueZ version may not support "
                          "the advertise menu - this is a known best-effort limitation, see module docstring)")

    def scan_once(self, scan_s: float, cancel: threading.Event) -> list[tuple[str, MeshMessage]]:
        """One passive scan window. Returns NEW (not-yet-seen) messages as (source_mac, message)."""
        ok, why = self.is_available()
        if not ok:
            log.warning("Mesh scan skipped: %s", why)
            return []
        try:
            proc = subprocess.Popen(["bluetoothctl", "--timeout", str(int(scan_s)), "scan", "on"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError:
            log.exception("Cannot start bluetoothctl for mesh scan")
            return []
        deadline = time.monotonic() + scan_s + 5
        try:
            while proc.poll() is None and time.monotonic() < deadline and not cancel.is_set():
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                out, _ = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
        found: list[tuple[str, MeshMessage]] = []
        for mac, data in parse_manufacturer_data(out or "", self.company_id).items():
            msg = decode_message(data)
            if msg is not None and not self._seen.seen_before(msg.msg_id):
                found.append((mac, msg))
        return found

    def relay_forward(self, msg: MeshMessage, advertise_s: float = 3.0) -> bool:
        """If TTL allows, rebroadcast with ttl-1. Returns whether a forward was attempted."""
        forwarded = relay(msg)
        if forwarded is None:
            return False
        self._broadcast(forwarded, advertise_s)
        return True
