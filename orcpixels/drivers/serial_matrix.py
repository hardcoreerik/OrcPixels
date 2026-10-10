"""USB / serial LED matrices (Teensy, Arduino, ESP32, Adafruit boards running an
Adalight or TPM2 receiver sketch). options.protocol: adalight | tpm2."""
from __future__ import annotations

import asyncio

from . import packets as PK
from .base import Driver, register


@register
class SerialMatrixDriver(Driver):
    name = "serial"
    hw_brightness = False
    hw_power = False

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.port = str(cfg.get("serial") or self.opts.get("serial") or "")
        self.baud = int(self.opts.get("baud", 115200))
        self.proto = str(self.opts.get("framing", "adalight")).lower()
        if self.proto not in ("adalight", "tpm2"):
            raise ValueError("serial framing must be adalight or tpm2")
        self.transport = f"serial/{self.proto}"
        self.ser = None

    def frame_bytes(self, data: bytes) -> bytes:
        return PK.adalight_frame(data) if self.proto == "adalight" else PK.tpm2_frame(data)

    async def connect(self) -> None:
        if not self.port:
            raise ConnectionError("serial: no port set")
        import serial  # pyserial
        self.ser = await asyncio.to_thread(serial.Serial, self.port, self.baud, timeout=1, write_timeout=2)
        self._connected = True

    async def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:  # noqa: BLE001
                pass
        self.ser = None
        self._connected = False

    async def send_frame(self, img) -> None:
        if self.ser is None:
            raise ConnectionError("serial: not connected")
        await self.throttle()
        await asyncio.to_thread(self.ser.write, self.frame_bytes(self.pixels(self.prepare(img))))
        self.sent_frames += 1
