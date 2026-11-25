#!/usr/bin/env python3
"""NETWORK RADAR entry point:  python3 main.py"""
from __future__ import annotations

import argparse
import logging
import sys
4
from utils.logger import setup_logging


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NETWORK RADAR - authorised local network discovery and monitoring")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--log-file", default=None, help="optional log file (device data is never written elsewhere)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level, args.log_file)
    log = logging.getLogger("main")
    try:
        import tkinter as tk
    except ImportError:
        log.error("tkinter is missing. Install it with: sudo apt install python3-tk")
        return 1
    try:
        import psutil  # noqa: F401
    except ImportError:
        log.error("psutil is missing. Run: pip install -r requirements.txt")
        return 1
    from core.application import ApplicationController
    from ui.dashboard import Dashboard

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.error("Cannot open a display: %s", exc)
        return 1
    Dashboard(root, ApplicationController())
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
