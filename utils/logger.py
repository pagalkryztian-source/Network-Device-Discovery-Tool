from __future__ import annotations

import logging
import sys
from typing import Optional

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Console logging; a file is written only if the user explicitly passes --log-file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=_FORMAT, handlers=handlers, force=True)
