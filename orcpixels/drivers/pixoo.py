"""Divoom Pixoo 64 (and other Wi-Fi Pixoo) local HTTP API: POST http://<ip>/post.

Frames go as Draw/SendHttpGif (base64 RGB, one request per frame, PicID from
Draw/GetHttpGifId). Divoom firmware is known to stall after a few hundred pushes, so the
PicID counter is reset every `reset_every` pushes (Draw/ResetHttpGifId)."""
from __future__ import annotations

import asyncio
import base64

from . import http as H
from .base import Driver, register


@register
class PixooDriver(Driver):
    name = "pixoo"
    animation = True
    transport = "http"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.host = str(cfg.get("host") or self.opts.get("host") or "")
        self.port = int(cfg.get("port") or self.opts.get("port") or 80)
        if self.width != self.height or self.width not in (16, 32, 64):
            raise ValueError("pixoo: PicWidth must be 16, 32 or 64 (square)")
        self.reset_every = int(self.opts.get("reset_every", 32))
        self.pic_id = 0
        self.pushes = 0
        self.opts.setdefault("max_anim_frames", 60)

    def _post(self, body: dict) -> dict:
        r = H.request(f"http://{self.host}:{self.port}/post", body)
        if r.get("error_code", 0) not in (0, None):
            raise RuntimeError(f"pixoo {body.get('Command')}: error_code {r.get('error_code')}")
        return r

    async def connect(self) -> None:
        if not self.host:
            raise ConnectionError("pixoo: no host set")
        await asyncio.to_thread(self._post, {"Command": "Draw/ResetHttpGifId"})
        r = await asyncio.to_thread(self._post, {"Command": "Draw/GetHttpGifId"})
        self.pic_id = int(r.get("PicId", 1))
        self._connected = True

    async def _next_id(self) -> int:
        self.pushes += 1
        if self.pushes % self.reset_every == 0:
            await asyncio.to_thread(self._post, {"Command": "Draw/ResetHttpGifId"})
            self.pic_id = 0
        self.pic_id += 1
        return self.pic_id

    def frame_body(self, data: bytes, pic_id: int, offset: int = 0, num: int = 1, speed: int = 1000) -> dict:
        return {"Command": "Draw/SendHttpGif", "PicNum": num, "PicWidth": self.width, "PicOffset": offset,
                "PicID": pic_id, "PicSpeed": speed, "PicData": base64.b64encode(data).decode()}

    async def send_frame(self, img) -> None:
        await self.throttle()
        pid = await self._next_id()
        await asyncio.to_thread(self._post, self.frame_body(self.pixels(self.prepare(img)), pid))
        self.sent_frames += 1

    async def send_animation(self, frames, durations_ms) -> None:
        frames = frames[: int(self.opts["max_anim_frames"])]
        pid = await self._next_id()
        speed = int(sum(durations_ms[:len(frames)]) / max(1, len(frames)))
        for i, f in enumerate(frames):
            await asyncio.to_thread(self._post, self.frame_body(self.pixels(self.prepare(f)), pid, i, len(frames),
                                                                max(20, speed)))
        self.sent_frames += 1

    async def _hw_brightness(self, level: int) -> None:
        await asyncio.to_thread(self._post, {"Command": "Channel/SetBrightness", "Brightness": int(level)})

    async def _hw_power(self, on: bool) -> None:
        await asyncio.to_thread(self._post, {"Command": "Channel/OnOffScreen", "OnOff": 1 if on else 0})
