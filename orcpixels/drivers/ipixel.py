"""iPixel Color BLE signs: the original OrcPixels path. This driver only wraps the
existing SignClient (ble_sign.py) behind the Driver interface; the web app keeps using
SignClient directly for iPixel displays, so behaviour is exactly unchanged."""
from __future__ import annotations

import io

from .base import Capabilities, Driver, register


@register
class IPixelDriver(Driver):
    name = "ipixel"
    status = "stable"
    animation = True
    transport = "ble"

    def __init__(self, cfg: dict, sign=None):
        super().__init__(cfg)
        if sign is None:
            from ..ble_sign import SignClient
            sign = SignClient(cfg.get("address") or None, display=cfg if cfg.get("id") else None)
        self.sign = sign

    @property
    def connected(self) -> bool:
        return self.sign.connected

    def capabilities(self) -> Capabilities:
        return Capabilities(self.sign.state.width, self.sign.state.height, 24, 4.0, True, 64, 100, True, True,
                            "ble", "stable")

    async def connect(self) -> None:
        await self.sign.connect()

    async def close(self) -> None:
        await self.sign.disconnect()

    async def send_frame(self, img) -> None:
        await self.sign.send_image(img)

    async def send_animation(self, frames, durations_ms) -> None:
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=list(durations_ms),
                       loop=0)
        await self.sign.send_gif(buf.getvalue(), frames[0])

    async def set_brightness(self, level: int) -> None:
        await self.sign.brightness(level)

    async def set_power(self, on: bool) -> None:
        await self.sign.power(on)
