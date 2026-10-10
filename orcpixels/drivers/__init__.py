"""LED matrix display library: driver interface (base.py), drivers, profile library
(profiles/*.json at the repo root) and DriverSign, the adapter that lets the whole
OrcPixels stack (presets, Live Data, playlists on the host, AI status) drive any panel."""
from __future__ import annotations

from . import ipixel, pixoo, serial_matrix, tidbyt, udp, virtual, wled  # noqa: F401  (register)
from .base import REGISTRY, Capabilities, Driver, Limits, NotSupported  # noqa: F401

STATUS = {"ipixel": "stable", "virtual": "stable", "wled": "experimental", "udp": "experimental",
          "pixoo": "experimental", "serial": "experimental", "tidbyt": "experimental"}


def driver_names() -> list[str]:
    return sorted(REGISTRY.drivers)


def make(cfg: dict) -> Driver:
    name = str(cfg.get("driver") or "ipixel")
    if name not in REGISTRY.drivers:
        raise ValueError(f"unknown driver {name!r} (have: {', '.join(driver_names())})")
    return REGISTRY.drivers[name](cfg)
