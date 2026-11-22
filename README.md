# 📡 NETWORK RADAR

**A passive network & Bluetooth radar with search mode and an off-grid emergency mesh relay — built to be honest about what it can and can't actually do.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-139%20passing-brightgreen.svg)](#tests)
[![No telemetry](https://img.shields.io/badge/data-local%20only%2C%20no%20cloud-success.svg)](#)

NETWORK RADAR discovers and visualizes devices on **your own network** (ARP, mDNS, SSDP) and nearby **Wi-Fi/Bluetooth broadcasts** in physical radio range, on a live animated radar — with device fingerprinting, signal-based distance estimation, a "search mode" to track one device by approach/retreat trend, and a Bluetooth mesh-lite relay for sending short emergency messages hop-to-hop with **zero internet, cellular, or network connection required**.

No database. No cloud. No telemetry. Everything lives in RAM and disappears when you close the window.

### ✨ Features
- 🖥️ Live radar view of devices on your LAN + nearby Wi-Fi/Bluetooth broadcasts
- 🔎 Device classification (phone, router, CCTV, IoT, etc.) with confidence scores and reasons — fully manual-overridable
- 📏 Signal-based distance estimation (calibratable) with honest, labeled error ranges
- 🎯 **Search mode** — track one device, get alerts when it's very close, goes offline, or reappears
- 📶 **Bluetooth mesh-lite relay** — broadcast short SOS/status messages that hop between nearby devices with no network of any kind
- 🔒 Passive by design — no joining networks, no exploitation, no credential attempts, no stealth

---

## ⚠️ Legal & Ethical Use

This tool is for monitoring **networks and devices you own or are explicitly authorized to monitor** (your home/office LAN, your own devices). Passive Wi-Fi/Bluetooth listening (radio-only mode) will show nearby broadcasts from devices you don't own, including their signal strength — treat that the way you'd treat overhearing a conversation in public: observe, don't act on it without consent.

- **Do not** use this against networks or devices you do not have permission to monitor. Unauthorized network scanning may be illegal in your jurisdiction.
- **The Bluetooth mesh relay is unencrypted and unauthenticated.** Don't rely on it as your only communication channel in a real emergency, and don't send sensitive information over it.
- **Distance/trend estimates are not GPS and not direction-finding.** They're RSSI-based approximations with real error margins (~±50%), explained in full in the docs below. Don't make safety-critical decisions based solely on this tool.
- This project does not and cannot see devices outside Bluetooth/Wi-Fi radio range, access other people's private networks, or access CCTV/surveillance systems. If anything claiming to do that is attached to this project, it is not something this codebase does.

The author(s) take no responsibility for misuse. By using this software you agree to use it lawfully and ethically.

---
