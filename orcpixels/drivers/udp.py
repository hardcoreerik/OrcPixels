"""Raw pixel streaming over UDP: DDP, E1.31 (sACN) and Art-Net. Used for ESP32 HUB75
panels (WLED-HUB75, ESP32-HUB75-MatrixPanel firmwares with a DDP / E1.31 / Art-Net
receiver, FPP, Falcon controllers, ...)."""
from __future__ import annotations

import asyncio
import socket

from . import packets as PK
from .base import Driver, register


class UdpSender:
    def __init__(self, host: str, port: int, multicast_ttl: int = 0):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if multicast_ttl:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, multicast_ttl)

    def send(self, pkts: list[bytes]) -> None:
        for p in pkts:
            self.sock.sendto(p, self.addr)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@register
class UdpPixelDriver(Driver):
    """options.transport: ddp | e131 | artnet. host required (e131 may use multicast)."""
    name = "udp"
    hw_brightness = False
    hw_power = False
    DEFAULT_PORTS = {"ddp": PK.DDP_PORT, "e131": PK.E131_PORT, "artnet": PK.ARTNET_PORT}

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.proto = str(self.opts.get("transport", "ddp")).lower()
        if self.proto not in self.DEFAULT_PORTS:
            raise ValueError(f"{self.name} transport must be one of {', '.join(self.DEFAULT_PORTS)}")
        self.transport = self.proto
        self.host = str(cfg.get("host") or self.opts.get("host") or "")
        self.universe = int(self.opts.get("universe", 1 if self.proto == "e131" else 0))
        self.per_universe = int(self.opts.get("channels_per_universe", 510))
        self.port = int(cfg.get("port") or self.opts.get("port") or self.DEFAULT_PORTS[self.proto])
        self.seq = 0
        self.cid = bytes.fromhex(self.opts["cid"]) if self.opts.get("cid") else PK.new_cid()
        self.tx: UdpSender | None = None

    async def connect(self) -> None:
        host = self.host
        if not host and self.proto == "e131":
            host = PK.e131_multicast(self.universe)
        if not host:
            raise ConnectionError(f"{self.name}: no host set")
        self.tx = UdpSender(host, self.port, multicast_ttl=4 if host.startswith("239.") else 0)
        self._connected = True

    async def close(self) -> None:
        if self.tx:
            self.tx.close()
        self.tx = None
        self._connected = False

    def packets_for(self, data: bytes) -> list[bytes]:
        self.seq += 1
        if self.proto == "ddp":
            return PK.ddp_packets(data, self.seq)
        blocks = PK.split_universes(data, self.per_universe)
        if self.proto == "e131":
            return [PK.e131_packet(self.universe + i, b, self.seq, self.cid) for i, b in enumerate(blocks)]
        return [PK.artdmx_packet(self.universe + i, b, self.seq) for i, b in enumerate(blocks)]

    async def send_frame(self, img) -> None:
        if not self.tx:
            raise ConnectionError(f"{self.name}: not connected")
        await self.throttle()
        pkts = self.packets_for(self.pixels(self.prepare(img)))
        await asyncio.to_thread(self.tx.send, pkts)
        self.sent_frames += 1
