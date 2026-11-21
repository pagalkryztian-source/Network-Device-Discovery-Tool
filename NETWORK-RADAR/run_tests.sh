#!/usr/bin/env bash
# Runs the whole suite with the standard library only (pytest also works if installed).
cd "$(dirname "$0")" && python3 -m unittest discover -s tests -t . -v
