"""GUI *logic* smoke test using a stub toolkit (real Tk is not needed or exercised).

This verifies event handling, filtering, table/radar/sidebar bookkeeping and manual overrides.
It does NOT verify rendering; run the application on a desktop for that.
"""
import itertools
import sys
import types
import unittest
from unittest import mock

STUB_MODULES = ("tkinter", "tkinter.ttk", "tkinter.messagebox")


class _Var:
    def __init__(self, *a, value="", **k): self._v = value
    def get(self): return self._v
    def set(self, v): self._v = v
    def trace_add(self, *a, **k): pass


class _Widget:
    _ids = itertools.count(1)

    def __init__(self, *a, **k): pass
    def __setitem__(self, k, v): pass
    def __getitem__(self, k): return None
    def __getattr__(self, name):
        if name.startswith("create_"):
            return lambda *a, **k: next(_Widget._ids)
        if name == "winfo_width": return lambda: 800
        if name == "winfo_height": return lambda: 500
        if name == "current": return lambda *a: 0
        if name == "get": return lambda *a: ""
        if name == "selection": return lambda: ()
        return mock.MagicMock()


def build_stub():
    tk = types.ModuleType("tkinter")
    for n in ("Frame", "Label", "Entry", "Canvas", "Button", "Text", "Checkbutton", "PanedWindow", "Tk", "Event",
              "Listbox"):
        setattr(tk, n, type(n, (_Widget,), {}))
    tk.StringVar = tk.BooleanVar = _Var
    tk.Variable = _Var
    tk.Misc = tk.Widget = _Widget
    tk.PIESLICE = "pieslice"
    tk.TclError = type("TclError", (Exception,), {})
    ttk = types.ModuleType("tkinter.ttk")
    for n in ("Combobox", "Spinbox", "Scrollbar", "Treeview", "Style"):
        setattr(ttk, n, type(n, (_Widget,), {}))
    mb = types.ModuleType("tkinter.messagebox")
    mb.showerror = mock.MagicMock(); mb.showwarning = mock.MagicMock(); mb.askyesno = mock.MagicMock(return_value=True)
    tk.ttk, tk.messagebox = ttk, mb
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb}


class TestDashboardLogic(unittest.TestCase):
    def setUp(self):
        self._patch = mock.patch.dict(sys.modules, build_stub())
        self._patch.start()
        for m in [m for m in sys.modules if m.startswith("ui.")]:
            del sys.modules[m]
        from core.application import ApplicationController
        from devices.device_registry import DeviceRegistry
        from devices.manufacturer_lookup import ManufacturerLookup
        from ui.dashboard import Dashboard
        self.Dashboard = Dashboard
        reg = DeviceRegistry(ManufacturerLookup([]))
        self.ctl = ApplicationController(registry=reg, interfaces=mock.MagicMock(list_interfaces=lambda: []))
        root = sys.modules["tkinter"].Tk()
        self.dash = Dashboard(root, self.ctl)

    def tearDown(self):
        self.dash.radar.stop()
        self._patch.stop()
        for m in [m for m in sys.modules if m.startswith("ui.")]:
            del sys.modules[m]

    def _feed(self, *observations):
        from core.event_manager import EventType
        mon = self.ctl.monitor
        for o in observations:
            mon._publish(self.ctl.registry.apply(o))
        self.dash._handle(self.ctl.poll_events())

    def test_device_events_reach_table_radar_and_sidebar(self):
        from devices.observation import Observation
        self._feed(Observation(source="arp", ip="10.0.0.5", mac="aa:aa:aa:aa:aa:05"),
                   Observation(source="mdns", ip="10.0.0.6", mdns_services={"_ipp._tcp"}))
        self.assertEqual(len(self.dash.table._devices), 2)
        self.assertEqual(len(self.dash.radar._blips), 2)
        self.assertEqual(len(self.dash.radar._pings), 2)                 # detection animation for new devices

    def test_filter_and_search(self):
        from devices.device import Category
        from devices.observation import Observation
        self._feed(Observation(source="arp", ip="10.0.0.5", mac="aa:aa:aa:aa:aa:05"),
                   Observation(source="mdns", ip="10.0.0.6", hostname="office-printer", mdns_services={"_ipp._tcp"}))
        self.dash._filter_changed(frozenset({Category.PRINTER}))
        self.assertEqual(list(self.dash.table._devices), ["ip:10.0.0.6"])
        self.assertFalse(self.dash.radar._blips["aa:aa:aa:aa:aa:05"].visible)
        self.dash._filter_changed(None)
        self.assertEqual(len(self.dash.table._devices), 2)

    def test_manual_override_updates_everything(self):
        from devices.device import Category
        from devices.observation import Observation
        self._feed(Observation(source="arp", ip="10.0.0.5", mac="aa:aa:aa:aa:aa:05"))
        self.dash._override("aa:aa:aa:aa:aa:05", Category.CCTV_CAMERA)
        self.assertIs(self.dash.radar._blips["aa:aa:aa:aa:aa:05"].category, Category.CCTV_CAMERA)
        self.assertEqual(self.ctl.registry.counts()[Category.CCTV_CAMERA], 1)

    def test_offline_and_error_events(self):
        from core.event_manager import EventType
        from devices.device import Status
        from devices.observation import Observation
        self._feed(Observation(source="arp", ip="10.0.0.5", mac="aa:aa:aa:aa:aa:05"))
        self.ctl.registry.missed_threshold = 1
        self.ctl.registry.begin_cycle()
        for dev in self.ctl.registry.end_cycle({"arp"}):
            self.ctl.bus.publish(EventType.DEVICE_OFFLINE, device=dev)
        self.ctl.bus.publish(EventType.SCAN_ERROR, message="arp: boom", fatal=True)
        self.ctl.bus.publish(EventType.CYCLE_COMPLETED, duration=1.0, online=0, total=1)
        self.dash._handle(self.ctl.poll_events())
        self.assertFalse(self.dash.radar._blips["aa:aa:aa:aa:aa:05"].online)
        self.assertIs(self.dash.table._devices["aa:aa:aa:aa:aa:05"].status, Status.OFFLINE)
        sys.modules["tkinter.messagebox"].showerror.assert_called()


if __name__ == "__main__":
    unittest.main()
