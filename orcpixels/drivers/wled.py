"""WLED (https://kno.wled.ge): JSON API for power / brightness / matrix size, pixels via
DDP (default, UDP 4048) or WLED's own UDP realtime DNRGB (UDP 21324)."""
from __future__ import annotations

import asyncio

from . import http as H
from . import packets as PK
from .base import register
from .udp import UdpPixelDriver, UdpSender


@register
class WledDriver(UdpPixelDriver):
    name = "wled"
    hw_brightness = True
    hw_power = True
    DEFAULT_PORTS = {"ddp": PK.DDP_PORT, "realtime": PK.WLED_RT_PORT}

    def __init__(self, cfg: dict):
        cfg = {**cfg, "options": {"transport": "ddp", **(cfg.get("protocol") or {}), **(cfg.get("options") or {})}}
        super().__init__(cfg)
        self.http_port = int(self.opts.get("http_port", 80))
        self.timeout_s = int(self.opts.get("realtime_timeout_s", 2))
        self.info: dict | None = None

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.http_port}{path}"

    async def connect(self) -> None:
        if not self.host:
            raise ConnectionError("wled: no host set")
        self.info = await asyncio.to_thread(H.request, self._url("/json/info"))
        m = ((self.info or {}).get("leds") or {}).get("matrix") or {}
        if m.get("w") and m.get("h") and self.opts.get("size_from_device", True):
            self.width, self.height = int(m["w"]), int(m["h"])
        self.tx = UdpSender(self.host, self.port)
        self._connected = True

    def packets_for(self, data: bytes) -> list[bytes]:
        if self.proto == "realtime":
            return PK.wled_dnrgb_packets(data, self.timeout_s)
        self.seq += 1
        return PK.ddp_packets(data, self.seq)

    async def _hw_brightness(self, level: int) -> None:
        await asyncio.to_thread(H.request, self._url("/json/state"), {"bri": round(level * 255 / 100)})

    async def _hw_power(self, on: bool) -> None:
        await asyncio.to_thread(H.request, self._url("/json/state"), {"on": bool(on)})
