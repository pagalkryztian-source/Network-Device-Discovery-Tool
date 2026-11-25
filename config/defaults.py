"""Static default values. Nothing here is persisted; see README (RAM-only design)."""
from __future__ import annotations

APP_NAME = "NETWORK RADAR"

# Scanning
DEFAULT_SCAN_INTERVAL_S = 30.0
MIN_SCAN_INTERVAL_S = 5.0
MAX_SCAN_INTERVAL_S = 3600.0
DEFAULT_MISSED_THRESHOLD = 3          # cycles with no observation before OFFLINE
DEFAULT_PACKET_RATE = 100             # ARP requests per second
MAX_PACKET_RATE = 1000
DEFAULT_BATCH_SIZE = 64               # ARP requests sent per srp() call
DEFAULT_CONCURRENCY = 16              # worker threads for hostname/port probes
MAX_CONCURRENCY = 64
DEFAULT_PROBE_RATE = 200              # TCP connection attempts per second (global)
ARP_TIMEOUT_S = 2.0
MAX_SUBNET_HOSTS = 1024               # refuse anything larger than ~/22

# Multicast discovery
MDNS_LISTEN_S = 3.0
SSDP_LISTEN_S = 3.0
SSDP_FETCH_TIMEOUT_S = 2.0
SSDP_MAX_DESCRIPTION_BYTES = 65536
BLUETOOTH_SCAN_S = 8

# Signal-based distance (log-distance path-loss model, see utils/signal.py)
DEFAULT_WIFI_REF_RSSI = -40.0         # dBm measured 1 m from a typical access point
DEFAULT_BT_REF_RSSI = -59.0           # dBm measured 1 m from a typical BLE device
DEFAULT_PATH_LOSS_EXP = 2.7           # ~2 free space, 2.5-4 indoors

# Enrichment
PROBE_TIMEOUT_S = 0.6
PROBE_REFRESH_S = 600.0               # re-probe ports at most this often
HOSTNAME_RETRY_S = 300.0
PROBE_PORTS = (22, 53, 80, 139, 443, 445, 515, 548, 554, 631, 3389,
               5000, 7547, 8008, 8009, 8554, 9100, 37777, 62078)

PORT_NAMES = {
    22: "ssh", 53: "dns", 80: "http", 139: "netbios-ssn", 443: "https",
    445: "smb", 515: "lpd", 548: "afp", 554: "rtsp", 631: "ipp",
    3389: "rdp", 5000: "upnp/dsm-http", 7547: "tr-069", 8008: "cast-http",
    8009: "cast", 8554: "rtsp-alt", 9100: "jetdirect", 37777: "dahua-sdk",
    62078: "ios-lockdown",
}

# Event queue
EVENT_QUEUE_MAX = 20000
GUI_POLL_MS = 100

# Search / track mode (honest limits: RSSI trend only, no bearing, no GPS of other devices)
TREND_EMA_ALPHA = 0.35                # smoothing for the distance-delta trend estimator
TREND_DEADBAND_M = 0.3                # ignore jitter smaller than this before calling it a trend
TREND_MIN_SAMPLES = 3                 # readings needed before a trend is reported at all
TARGET_CLOSE_THRESHOLD_M = 5.0        # "very close" alert fires once per approach below this
