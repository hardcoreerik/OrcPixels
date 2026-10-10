"""Virtual display: no hardware. Keeps the last panel-sized frame for the browser preview
(GET /api/device/frame.png) and writes PNG / GIF files to out/virtual/<display id>/."""
from __future__ import annotations

import io
from pathlib import Path

from .base import Driver, register

from ..paths import OUT


@register
class VirtualDriver(Driver):
    name = "virtual"
    status = "stable"
    animation = True
    transport = "none (preview)"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.out = Path(self.opts.get("out_dir") or OUT / "virtual" / str(cfg.get("id") or "virtual"))
        self.write_files = bool(self.opts.get("write_files", True))
        self.opts.setdefault("max_anim_frames", 400)
        self.last = None
        self.last_gif: bytes | None = None

    async def send_frame(self, img) -> None:
        self.last = self.prepare(img)
        self.last_gif = None
        self.sent_frames += 1
        if self.write_files:
            self.out.mkdir(parents=True, exist_ok=True)
            self.last.save(self.out / "last.png")

    async def send_animation(self, frames, durations_ms) -> None:
        fr = [self.prepare(f) for f in frames]
        buf = io.BytesIO()
        fr[0].save(buf, format="GIF", save_all=True, append_images=fr[1:], duration=list(durations_ms[:len(fr)]),
                   loop=0, disposal=1)
        self.last, self.last_gif = fr[0], buf.getvalue()
        self.sent_frames += 1
        if self.write_files:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "last.gif").write_bytes(self.last_gif)
            fr[0].save(self.out / "last.png")
