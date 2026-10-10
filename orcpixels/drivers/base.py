"""Display driver interface (the LED matrix display library).

A driver turns frames into one kind of panel. OrcPixels renders content at a curated
size (fit.render_size), then the driver's prepare() fits it to the panel, applies the
profile's rotation / flips / pixel layout and its safety limits, and sends it.

    caps = drv.capabilities()          # size, colour depth, max fps, animation, slots, ...
    await drv.connect()
    await drv.send_frame(img)          # one still frame (PIL image, any size)
    await drv.send_animation(frames, durations_ms)   # native animation, if caps.animation
    await drv.set_brightness(0..100); await drv.set_power(True/False)
    await drv.close()

Drivers never wipe device storage and have no "clear" beyond a black frame.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field

from PIL import Image

from .. import fit as FIT


class NotSupported(RuntimeError):
    """The panel/driver can't do this (e.g. on-device slots on a pixel-stream panel)."""


@dataclass
class Capabilities:
    width: int
    height: int
    color_depth: int = 24              # bits per pixel on the wire
    max_fps: float = 30.0
    animation: bool = False            # stores / plays an animation by itself
    max_anim_frames: int = 0
    on_device_slots: int = 0
    brightness: bool = True            # hardware brightness control
    power: bool = True
    transport: str = ""
    status: str = "experimental"       # stable | experimental

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Limits:
    """Profile safety limits."""
    max_fps: float = 30.0
    max_brightness: int = 100          # %, clamps set_brightness; scales pixels if no hw control
    max_frame_bytes: int = 0           # 0 = no limit (encoded size for upload drivers)
    power_limit: float = 1.0           # 0..1: cap on the average pixel level (PSU budget)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Limits":
        d = d or {}
        return cls(max_fps=float(d.get("max_fps", 30.0)), max_brightness=int(d.get("max_brightness", 100)),
                   max_frame_bytes=int(d.get("max_frame_bytes", 0)), power_limit=float(d.get("power_limit", 1.0)))


@dataclass
class Layout:
    """How pixels are ordered on the wire for raw pixel streams."""
    rotation: int = 0                  # 0 / 90 / 180 / 270, clockwise
    flip_x: bool = False
    flip_y: bool = False
    serpentine: bool = False           # every other row reversed (LED strips zig-zagged)
    order: str = "rgb"                 # rgb | grb | bgr | ...

    @classmethod
    def from_dict(cls, d: dict | None) -> "Layout":
        d = d or {}
        return cls(rotation=int(d.get("rotation", 0)) % 360, flip_x=bool(d.get("flip_x", False)),
                   flip_y=bool(d.get("flip_y", False)), serpentine=bool(d.get("serpentine", False)),
                   order=str(d.get("order", "rgb")).lower())


class Driver:
    name = "base"
    status = "experimental"
    color_depth = 24
    hw_brightness = True
    hw_power = True
    animation = False
    transport = ""

    def __init__(self, cfg: dict):
        """cfg: profile merged with the display entry (width, height, host, port, serial,
        options, layout, limits)."""
        self.cfg = cfg
        self.width, self.height = int(cfg["width"]), int(cfg["height"])
        self.opts = {**(cfg.get("protocol") or {}), **(cfg.get("options") or {})}
        self.limits = Limits.from_dict(cfg.get("limits"))
        self.layout = Layout.from_dict(cfg.get("layout"))
        self.level = self.limits.max_brightness       # software brightness (0..100)
        self.on = True
        self._connected = False
        self._last_send = 0.0
        self.sent_frames = 0

    # -- interface -------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    def capabilities(self) -> Capabilities:
        return Capabilities(self.width, self.height, self.color_depth, self.limits.max_fps, self.animation,
                            int(self.opts.get("max_anim_frames", 0)) if self.animation else 0, 0,
                            self.hw_brightness, self.hw_power, self.transport, self.status)

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def send_frame(self, img: Image.Image) -> None:
        raise NotImplementedError

    async def send_animation(self, frames: list[Image.Image], durations_ms: list[int]) -> None:
        raise NotSupported(f"{self.name}: no native animation (frames are streamed)")

    async def set_brightness(self, level: int) -> None:
        self.level = max(0, min(int(level), self.limits.max_brightness))
        if self.hw_brightness:
            await self._hw_brightness(self.level)

    async def set_power(self, on: bool) -> None:
        self.on = bool(on)
        if self.hw_power:
            await self._hw_power(self.on)
        elif not self.on:
            await self.send_frame(Image.new("RGB", (self.width, self.height)))

    async def _hw_brightness(self, level: int) -> None:
        pass

    async def _hw_power(self, on: bool) -> None:
        pass

    # -- helpers -----------------------------------------------------------------
    def prepare(self, img: Image.Image) -> Image.Image:
        """Fit to the panel, apply rotation / flips and the brightness / power limits."""
        out = FIT.fit(img, *self._wire_size_before_rotation())
        lay = self.layout
        if lay.rotation:
            out = out.rotate(-lay.rotation, expand=True)
        if lay.flip_x:
            out = out.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if lay.flip_y:
            out = out.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        scale = 1.0
        if not self.hw_brightness:
            scale *= self.level / 100.0
        if not self.on and not self.hw_power:
            scale = 0.0
        if self.limits.power_limit < 1.0:
            px = out.tobytes()
            avg = (sum(px) / (len(px) * 255.0)) if px else 0.0
            if avg * scale > self.limits.power_limit:
                scale = self.limits.power_limit / avg
        if scale < 0.999:
            out = out.point(lambda v: int(v * scale))
        return out

    def _wire_size_before_rotation(self) -> tuple[int, int]:
        """width x height is the *viewing* size (how the panel is looked at); layout.rotation
        is the physical mounting, applied after composing (a 16x64 portrait view on a 64x16
        panel mounted on its side: width 16, height 64, rotation 90)."""
        return self.width, self.height

    def pixels(self, img: Image.Image) -> bytes:
        """Prepared image -> wire bytes in row order (serpentine / colour order applied)."""
        w, h = img.size
        raw = img.convert("RGB").tobytes()
        if self.layout.serpentine:
            rows = [raw[y * w * 3:(y + 1) * w * 3] for y in range(h)]
            rows = [r if y % 2 == 0 else b"".join(r[x * 3:x * 3 + 3] for x in range(w - 1, -1, -1))
                    for y, r in enumerate(rows)]
            raw = b"".join(rows)
        order = self.layout.order
        if order != "rgb":
            idx = ["rgb".index(c) for c in order]
            b = bytearray(len(raw))
            for i in range(0, len(raw), 3):
                b[i], b[i + 1], b[i + 2] = raw[i + idx[0]], raw[i + idx[1]], raw[i + idx[2]]
            raw = bytes(b)
        return raw

    async def throttle(self) -> None:
        """Respect limits.max_fps between sends."""
        gap = 1.0 / self.limits.max_fps if self.limits.max_fps > 0 else 0.0
        wait = self._last_send + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()

    def describe(self) -> dict:
        return {"driver": self.name, "status": self.status, "capabilities": self.capabilities().as_dict(),
                "limits": self.limits.__dict__, "layout": self.layout.__dict__}


@dataclass
class _Reg:
    drivers: dict = field(default_factory=dict)


REGISTRY = _Reg()


def register(cls):
    REGISTRY.drivers[cls.name] = cls
    return cls
