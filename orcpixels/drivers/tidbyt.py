"""Tidbyt (api.tidbyt.com) and Tronbyt (self-hosted, same push API).

POST {base}/v0/devices/{device_id}/push, Authorization: Bearer <key>,
{"image": base64 WebP, "installationID": ..., "background": false}. The API key comes from
options.token, else the environment variable options.token_env (default TIDBYT_API_KEY)."""
from __future__ import annotations

import asyncio
import base64
import io
import os

from . import http as H
from .base import Driver, register


@register
class TidbytDriver(Driver):
    name = "tidbyt"
    animation = True
    hw_power = False
    hw_brightness = False
    transport = "https push"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.base = str(cfg.get("host") or self.opts.get("base_url") or "https://api.tidbyt.com").rstrip("/")
        if not self.base.startswith("http"):
            self.base = "http://" + self.base
        self.device = str(self.opts.get("device_id") or "")
        self.installation = str(self.opts.get("installation_id") or "orcpixels")
        self.opts.setdefault("max_anim_frames", 100)

    def token(self) -> str:
        return str(self.opts.get("token") or os.environ.get(str(self.opts.get("token_env") or "TIDBYT_API_KEY")) or "")

    async def connect(self) -> None:
        if not self.device:
            raise ConnectionError("tidbyt: options.device_id not set")
        if not self.token():
            raise ConnectionError("tidbyt: no API key (options.token or TIDBYT_API_KEY)")
        self._connected = True

    def webp(self, frames, durations_ms) -> bytes:
        buf = io.BytesIO()
        fr = [self.prepare(f) for f in frames]
        if len(fr) == 1:
            fr[0].save(buf, format="WEBP", lossless=True)
        else:
            fr[0].save(buf, format="WEBP", lossless=True, save_all=True, append_images=fr[1:],
                       duration=list(durations_ms[:len(fr)]), loop=0)
        return buf.getvalue()

    def push_body(self, webp: bytes) -> dict:
        return {"image": base64.b64encode(webp).decode(), "installationID": self.installation, "background": False}

    async def _push(self, webp: bytes) -> None:
        url = f"{self.base}/v0/devices/{self.device}/push"
        await asyncio.to_thread(H.request, url, self.push_body(webp), {"Authorization": f"Bearer {self.token()}"},
                                10.0)
        self.sent_frames += 1

    async def send_frame(self, img) -> None:
        await self.throttle()
        await self._push(self.webp([img], [1000]))

    async def send_animation(self, frames, durations_ms) -> None:
        await self.throttle()
        n = int(self.opts["max_anim_frames"])
        await self._push(self.webp(frames[:n], durations_ms[:n]))
