"""DriverSign: the SignClient interface on top of any Driver, so the whole OrcPixels stack
(presets, creator, Live Data, OrcSDR Live, AI status, link supervisor, activity log) runs
unchanged on non-iPixel panels. Content is rendered at the curated size
(displays.render_size) and fitted to the panel by the driver.

iPixel-only features (on-device program slots / saved playlists, device orientation,
native device text) raise NotSupported; native text is rendered as an image instead."""
from __future__ import annotations

import asyncio
import io
import logging
import time

from PIL import Image

from .. import activity as A
from .. import displays as D
from .. import fit as FIT
from .. import protocol as P
from ..ble_sign import SignClient, TakenOver
from . import make
from .base import NotSupported

log = logging.getLogger(__name__)


class DriverSign(SignClient):
    def __init__(self, display: dict, driver=None):
        super().__init__(None, display=display)
        from .. import profiles as PF
        self.driver = driver or make(PF.config_for(display))
        self.state.width, self.state.height = D.render_size(display)
        self.state.name = display.get("label") or display.get("id")
        self.state.address = display.get("host") or display.get("serial") or self.driver.name
        self.last_device_frame: Image.Image | None = None
        self._anim: asyncio.Task | None = None

    # ---------------------------------------------------------------- link
    @property
    def connected(self) -> bool:
        return bool(self.driver.connected)

    async def _connect_locked(self, attempts: int = 3, strict: bool = False):
        if self.connected:
            return
        err: Exception | None = None
        for i in range(max(1, attempts)):
            try:
                await self.driver.connect()
                self.state.connected = True
                self.state.last_error = None
                self.state.device_info = {"driver": self.driver.name, "width": self.driver.width,
                                          "height": self.driver.height, "status": self.driver.status}
                self.state.width, self.state.height = FIT.render_size(self.driver.width, self.driver.height)
                if self.state.brightness is not None:
                    try:
                        await self.driver.set_brightness(self.state.brightness)
                    except Exception as e:  # noqa: BLE001
                        log.warning("re-applying brightness failed: %s", e)
                self._had_link = True
                self.link_lost_at = None
                self._content_deadline = None          # no on-device playlist to fall back to
                return
            except Exception as e:  # noqa: BLE001
                err = e
                self.state.last_error = f"{type(e).__name__}: {e}"
                log.warning("%s connect attempt %d failed: %s", self.driver.name, i + 1, self.state.last_error)
                if i < attempts - 1:
                    await asyncio.sleep(1.0 * (i + 1))
        raise ConnectionError(f"could not connect: {err}")

    async def _safe_disconnect(self):
        await self._stop_anim()
        try:
            await self.driver.close()
        except Exception:  # noqa: BLE001
            pass
        self.state.connected = False

    async def _check_idle(self):
        self._content_deadline = None

    # ------------------------------------------------------- iPixel-only commands
    async def run(self, cmd: P.Command, retry: bool = True, progress=None, supersedable: bool = False):
        raise NotSupported(f"{cmd.name}: iPixel-only command; the {self.driver.name} driver has no on-device "
                           "slots / programs (OrcPixels plays content from the host instead)")

    async def _ensure_orientation(self, value: int):
        return None

    async def orientation(self, value: int) -> None:
        raise NotSupported("device orientation is iPixel-only; use the rotate setting or the profile rotation")

    # -------------------------------------------------------------- sending
    async def _stop_anim(self):
        t, self._anim = self._anim, None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass

    async def _show_locked(self, upright: Image.Image):
        await self._connect_locked(1 if self.supervised else 3)
        await self.driver.send_frame(self._orient(upright))
        self.last_device_frame = FIT.fit(self._orient(upright), self.driver.width, self.driver.height)

    def _fit(self, img: Image.Image) -> Image.Image:
        w, h = self.state.width, self.state.height
        img = img.convert("RGB")
        return img if img.size == (w, h) else P.fit_image(img, w, h)

    async def _send(self, what: str, fn, nbytes: int = 0, supersedable: bool = True):
        ent = self.activity.start(what, nbytes)
        ent["status"], ent["display"] = "queued", supersedable
        epoch = self._supersede_epoch
        try:
            async with self._lock:
                if supersedable and epoch != self._supersede_epoch:
                    from ..ble_sign import Superseded
                    raise Superseded(f"skipped: {self._supersede_reason or 'superseded'} after it was queued")
                self.activity.mark(ent, "sending")
                await fn()
        except BaseException as e:
            self.activity.finish(ent, "failed", error=f"{type(e).__name__}: {e}")
            raise
        self.activity.finish(ent, "sent")

    async def send_image(self, img: Image.Image) -> None:
        upright = self._fit(img)
        await self._stop_anim()
        await self._send(f"{self.driver.name} frame", lambda: self._show_locked(upright),
                         self.driver.width * self.driver.height * 3)
        self.last_gif = None
        self.last_frame = upright
        self.resume_hint = None
        self._mark_content()
        self.display_seq += 1
        self.set_showing("image", A.LABEL.get() or "image")

    async def send_text(self, text: str, *, color: str = "ffffff", bg_color: str | None = None,
                        animation: int = P.TextAnimation.STATIC, speed: int = 80, rainbow: int = 0) -> None:
        from ..render import preview_native_text
        img = preview_native_text(text, color, bg_color, self.state.width, self.state.height)
        await self.send_image(img)
        self.set_showing("text", text[:40])

    async def send_gif(self, gif_bytes: bytes, first_frame: Image.Image | None = None) -> None:
        from ..render import encode_gif, gif_frames
        frames, durs = gif_frames(gif_bytes)
        frames = [self._fit(f) for f in frames]
        durs = [max(20, int(d or 100)) for d in durs]
        await self._stop_anim()
        drv = self.driver
        if len(frames) == 1:
            await self.send_image(frames[0])
            return
        if drv.animation:
            async def native():
                await self._connect_locked(1 if self.supervised else 3)
                await drv.send_animation([self._orient(f) for f in frames], durs)
                self.last_device_frame = FIT.fit(self._orient(frames[0]), drv.width, drv.height)
            await self._send(f"{drv.name} animation", native, len(gif_bytes))
        else:
            await self._send(f"{drv.name} animation (streamed)", lambda: self._show_locked(frames[0]),
                             len(gif_bytes))
            self._anim = asyncio.get_running_loop().create_task(self._stream(frames, durs))
        self.last_gif = encode_gif(frames, durs)
        self.last_frame = first_frame or frames[0]
        self.resume_hint = None
        self._mark_content()
        self.display_seq += 1
        self.set_showing("animation", A.LABEL.get() or "animation", frames=len(frames))

    async def _stream(self, frames, durs):
        """Host-side playback for panels that can't store an animation."""
        i = 1
        try:
            while True:
                t0 = time.monotonic()
                await asyncio.sleep(durs[(i - 1) % len(frames)] / 1000.0)
                async with self._lock:
                    await self._show_locked(frames[i % len(frames)])
                i += 1
                if time.monotonic() - t0 > 30:          # stalled link: let the keeper recover
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.state.last_error = f"animation stream: {type(e).__name__}: {e}"
            log.warning("%s", self.state.last_error)

    async def send_live_png(self, png: bytes, upright: Image.Image, chunk: int = 244, response: bool = False,
                            ack_timeout: float | None = None, timeout: float | None = None,
                            expect_seq: int | None = None) -> bytes:
        await self._stop_anim()
        async with self._lock:
            if expect_seq is not None and self.display_seq != expect_seq:
                raise TakenOver("another display command took the sign before this Live frame")
            try:
                async with asyncio.timeout(timeout):
                    await self._show_locked(self._fit(upright))
            except BaseException as e:
                self.activity.live_frame(len(png), False, error=f"{type(e).__name__}: {e}")
                if not isinstance(e, Exception):
                    raise
                self.state.last_error = f"send_live_png: {type(e).__name__}: {e}"
                await self._safe_disconnect()
                self._wake()
                raise
        self.activity.live_frame(len(png), True, b"")
        self.live_seq = self.display_seq
        self.last_gif = None
        self.last_frame = upright
        self.resume_hint = None
        self._mark_content()
        if not self.showing or self.showing.get("kind") != "live":
            self.set_showing("live", "OrcSDR Live")
        return b""

    async def brightness(self, level: int) -> None:
        async with self._lock:
            await self._connect_locked(1 if self.supervised else 3)
            await self.driver.set_brightness(int(level))
        self.state.brightness = int(level)

    async def power(self, on: bool) -> None:
        async with self._lock:
            await self._connect_locked(1 if self.supervised else 3)
            await self.driver.set_power(bool(on))
        self.state.power = bool(on)

    def device_png(self) -> bytes | None:
        img = getattr(self.driver, "last", None) or self.last_device_frame
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def make_sign(display: dict):
    """SignClient for iPixel displays (legacy path, unchanged), DriverSign for the rest."""
    if D.is_ipixel(display):
        return SignClient(display["address"], display=display)
    return DriverSign(display)
