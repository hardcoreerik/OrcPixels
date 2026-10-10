"""Live Data: the 15 data screens (orcpixels/livedata_screens.py) streamed to the sign.

Same stream engine and takeover rules as OrcSDR Live (DataLive subclasses OrcSDRLive):
  * one live source at a time: starting Live Data stops OrcSDR Live / Auto status and
    vice versa (OrcSDRLive.peers);
  * any preset/text/image/playlist send takes the sign -> Live Data disarms and says why;
  * the SignClient 30 s idle rule and the playlist fallback still apply;
  * "lost" = no selected screen has usable data (never fetched, or older than the feed's
    max_stale) -> playlist fallback, probe the sources politely, resume when they answer.
A feed that fails after a success keeps its last good value, drawn with a red stale dot.

Frames go out at a modest ~1 fps (data does not change faster; animations are 2-4 phase
icons + paged detail lines). Rotate mode cycles the selected screens every rotate_s.
Every source fetch is due-based (Feed.poll_s), so previews / probes never hammer an API.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Optional

from PIL import Image
from pydantic import BaseModel

from . import livedata_screens as SC
from . import settings as S
from .orcsdr_live import OrcSDRLive, png_bytes

log = logging.getLogger("orcpixels.livedata")

SETTING_ARMED = "livedata_armed"
SETTING_SCREENS = "livedata_screens"
SETTING_ROTATE = "livedata_rotate"
SETTING_ROTATE_S = "livedata_rotate_s"
SETTING_INTERVAL = "livedata_interval"
SETTING_TRANSPORT = "livedata_transport"   # legacy (default) | fast | fast-rgb | fast-acked
DEFAULT_SCREENS = ["wx_now"]
DEFAULT_ROTATE_S = 20.0
DEFAULT_INTERVAL = 1.0           # s between frames (data screens don't need 4 fps)
MIN_INTERVAL = 0.5
TICK_S = 1.0                     # sampler tick (kicks due feed refreshes)


class NoData(RuntimeError):
    pass


class _Probe:
    """Client adapter for the OrcSDR engine: status() = 'is any selected source usable?'.
    Refreshes are due-based (Feed.poll_s / backoff), never forced."""

    def __init__(self, live: "DataLive"):
        self.live, self.base_url = live, "livedata"

    async def status(self):
        feeds = self.live.active_feeds()
        await asyncio.gather(*(f.refresh() for f in feeds), return_exceptions=True)
        ok = [f.key for f in feeds if f.usable]
        if not ok:
            errs = "; ".join(f"{f.key}: {f.error or 'no data yet'}" for f in feeds)
            raise NoData(errs or "no screens selected")
        return {"usable": ok}

    async def spectrum_raw(self):
        return []


class DataLive(OrcSDRLive):
    NAME = "Live Data"
    TRANSPORT_KEY = SETTING_TRANSPORT

    def __init__(self, sign, auto=None, fallback=None, persist: bool = False, feeds=None):
        super().__init__(sign, auto, client=_Probe.__new__(_Probe), fallback=fallback,
                         persist=persist)
        self.client = _Probe(self)
        self.feeds = feeds if feeds is not None else SC.make_feeds()
        cfg = S.load() if persist else {}
        scr = [k for k in (cfg.get(SETTING_SCREENS) or DEFAULT_SCREENS) if k in SC.SCREENS]
        self.screens: list[str] = scr or list(DEFAULT_SCREENS)
        self.rotate = bool(cfg.get(SETTING_ROTATE, False))
        self.rotate_s = float(cfg.get(SETTING_ROTATE_S, DEFAULT_ROTATE_S))
        self.interval = max(MIN_INTERVAL, float(cfg.get(SETTING_INTERVAL, DEFAULT_INTERVAL)))
        self.waterfall = False
        self.current = self.screens[0]
        self._k = 0
        self._kmem: dict[str, int] = {}
        self._cur_since = time.monotonic()
        self._refreshing: set = set()
        # watchdog tunables: sources answer in seconds and are polled every minutes
        self.poll_s = TICK_S
        self.http_timeout_s = 15.0        # _get bound = 30.5 s (Feed caps a fetch at 26 s)
        self.loss_fails, self.loss_min_s = 3, 3.0
        self.probe_s, self.probe_max_s = 5.0, 15.0
        self.confirm_s, self.resume_oks = 1.0, 1

    # ---- selection
    def active_feeds(self):
        keys = self.screens if self.rotate else [self.current if self.current in self.screens else self.screens[0]]
        return [self.feeds[k] for k in keys if k in self.feeds]

    def url(self) -> str:
        return "livedata"

    async def send_test_frame(self, key: str | None = None) -> dict:
        """One frame of a screen on the configured transport, WITHOUT arming Live Data, so
        the user can confirm with their eyes that this transport draws on the sign (no
        notify / reply tells us: frozen frames are acked like good ones)."""
        if self.enabled:
            raise RuntimeError("Live Data is running: its frames are already on the sign")
        key = key or self.current
        if key not in self.feeds:
            raise ValueError(f"unknown screen {key}")
        f = self.feeds[key]
        await f.refresh()
        img = SC.render(key, f, 0, size=self.size())
        self._seq = None
        from .activity import labelled
        with labelled(f"Live Data test frame ({self.transport})"):
            await self._send_frame(img)
        if hasattr(self.sign, "display_seq") and self.transport != "legacy":
            self.sign.display_seq += 1           # a display send: a running mode must notice
        if hasattr(self.sign, "set_showing"):
            self.sign.set_showing("image", f"Live Data test frame: {f.title} ({self.transport})")
        return {"screen": key, "transport": self.transport, "at": time.time()}

    def set_selection(self, screens=None, rotate=None, rotate_s=None, interval=None):
        if screens is not None:
            scr = [k for k in dict.fromkeys(screens) if k in SC.SCREENS]
            if not scr:
                raise ValueError("pick at least one known screen")
            self.screens = scr
            rot = self.rotate if rotate is None else bool(rotate)
            if self.current not in scr or not rot:      # single screen: the first one is the pick
                self._switch(scr[0])
        if rotate is not None:
            self.rotate = bool(rotate)
        if rotate_s is not None:
            self.rotate_s = max(5.0, min(3600.0, float(rotate_s)))
        if interval is not None:
            self.interval = max(MIN_INTERVAL, min(60.0, float(interval)))
        if self.current not in self.screens:
            self._switch(self.screens[0])

    def _switch(self, key: str):
        """Rotation resumes each screen where it left off (a 60-frame headline loop
        would otherwise show only its first headlines every rotate_s)."""
        if key != self.current:
            self._kmem[self.current] = self._k
            self.current, self._k, self._cur_since = key, self._kmem.get(key, 0), time.monotonic()
            self._last_bytes = None

    def _pick_rotation(self):
        if not self.rotate or len(self.screens) < 2:
            return
        if time.monotonic() - self._cur_since < self.rotate_s:
            return
        order = self.screens
        i = order.index(self.current) if self.current in order else -1
        for step in range(1, len(order) + 1):           # next screen that has data
            k = order[(i + step) % len(order)]
            if self.feeds[k].usable:
                self._switch(k)
                return
        self._cur_since = time.monotonic()

    # ---- sampling (overrides)
    def _kick(self, f):
        if f.key in self._refreshing or not f.due():
            return
        t = asyncio.create_task(f.refresh())
        self._refreshing.add(f.key)
        t.add_done_callback(lambda _t, k=f.key: self._refreshing.discard(k))

    async def sample_once(self, spectrum: bool = True):
        try:
            self.status = await self._get(self.client.status())
            self._poll_ok()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._poll_failed(e)
            self.status = None
        return self.status

    async def _sampler(self):
        """1 s tick: start due refreshes in the background (a slow API never blocks the
        sender), feed the watchdog: lost only when no selected screen has usable data
        and no refresh is still in flight."""
        while True:
            t0 = time.monotonic()
            feeds = self.active_feeds()
            ok = [f.key for f in feeds if f.usable]
            if ok:
                self.status = {"usable": ok}
                self._poll_ok()
            elif not any(f.key in self._refreshing for f in feeds):   # judged before new kicks
                self._poll_failed(NoData("; ".join(f"{f.key}: {f.error or 'no data'}" for f in feeds)))
            for f in feeds:
                self._kick(f)
            await asyncio.sleep(max(0.05, self.poll_s - (time.monotonic() - t0)))

    def render(self) -> Image.Image:
        self._pick_rotation()
        if not self.rotate and self.current not in self.screens:
            self._switch(self.screens[0])
        img = SC.render(self.current, self.feeds[self.current], self._k, size=self.size())
        self._k += 1
        return img

    def showing_name(self) -> str:
        return f"Live Data: {self.feeds[self.current].title}"

    def size(self) -> tuple[int, int]:
        """Render size = the panel this engine drives (64x16 strip or 64x64 matrix)."""
        st = getattr(self.sign, "state", None)
        try:
            return (int(st.width), int(st.height)) if st is not None else (64, 16)
        except Exception:  # noqa: BLE001
            return (64, 16)

    # ---- persistence / status
    def _save_armed(self, armed: bool):
        if not self.persist:
            return
        try:
            upd = {SETTING_ARMED: bool(armed)}
            if armed:
                upd.update(self._cfg())
            S.save(upd)
        except Exception as e:  # noqa: BLE001
            log.warning("live data: could not save armed state: %s", e)

    def _cfg(self) -> dict:
        return {SETTING_SCREENS: list(self.screens), SETTING_ROTATE: self.rotate,
                SETTING_ROTATE_S: self.rotate_s, SETTING_INTERVAL: self.interval,
                SETTING_TRANSPORT: self.transport}

    async def start(self, screens=None, rotate=None, rotate_s=None, interval=None):
        self.set_selection(screens, rotate, rotate_s, interval)
        if self.enabled:                       # already running: switch screens in place
            self._save_armed(True)
            for f in self.active_feeds():
                self._kick(f)
            return
        self._k = 0
        self._cur_since = time.monotonic()
        await super().start(interval=self.interval)

    def label(self) -> str:
        if self.state != "off" and self.ble_paused:
            return "paused: sign reconnecting"
        title = self.feeds[self.current].title
        if self.state == "streaming":
            if self._fallback_active:
                return "data back; resuming"
            return f"streaming {title}" + (f" (rotating {len(self.screens)})" if self.rotate and len(self.screens) > 1 else "")
        if self.state == "waiting":
            what = self.showing or ("fallback failed: " + (self.fallback_error or "?"))
            return f"waiting for data (showing {what})"
        return "off"

    def info(self) -> dict:
        d = super().info()
        d.pop("waterfall", None)
        from .orcsdr_live import TRANSPORTS
        d.update(transports=TRANSPORTS)
        d.update(name=self.NAME, screens=list(self.screens), rotate=self.rotate, rotate_s=self.rotate_s,
                 current=self.current, current_title=self.feeds[self.current].title,
                 data_error=self.device_error)
        return d


# ------------------------------------------------------------------ routes
class DataLiveReq(BaseModel):
    action: str = "start"                 # start | stop
    screens: Optional[list[str]] = None
    rotate: Optional[bool] = None
    rotate_s: Optional[float] = None
    interval: Optional[float] = None


class TestFrameReq(BaseModel):
    screen: Optional[str] = None


class FeedCfgReq(BaseModel):
    values: dict[str, Optional[str]] = {}


def feed_config() -> dict:
    """Markets/news settings (symbols, currencies, RSS URLs) with defaults. Secrets are
    reported as set / not set only, never echoed."""
    from .sources import ft8feed as FT
    from .sources import marketnews as MN
    from . import place as PLACE
    cur = S.load()
    return {"fields": [{"key": k, "label": lab, "kind": kind, "default": d,
                        "value": "" if cur.get(k) is None else cur.get(k)}
                       for k, (d, kind, lab) in {**PLACE.CONFIG, **MN.CONFIG, **FT.CONFIG}.items()],
            "secrets": {k: bool(cur.get(k)) or bool(os.environ.get("X_BEARER_TOKEN")) for k in MN.SECRET_KEYS}}


class DataCfgReq(BaseModel):
    transport: Optional[str] = None
    screens: Optional[list[str]] = None
    rotate: Optional[bool] = None
    rotate_s: Optional[float] = None
    interval: Optional[float] = None


def screen_list(live: DataLive) -> list[dict]:
    now = time.time()
    out = []
    for k, scr in SC.SCREENS.items():
        f = live.feeds[k]
        out.append({"key": k, "title": f.title, "source": f.source, "url": _safe_url(f), "poll_s": f.poll_s,
                    "key_env": f.key_env, "needs_key": bool(getattr(f, "needs_key", False)), "local": f.local,
                    "group": scr.group, "optional": k in SC.OPTIONAL,
                    "loop": SC.loop_len(k, f, live.size()), "selected": k in live.screens,
                    "age_s": None if f.ok_at is None else round(now - f.ok_at), **f.state()})
    return out


def led_gif(key: str, feed, scale: int = 4, now: float | None = None, size=None) -> bytes:
    """Seamless animated LED-dot preview of one screen loop (studio.led_frame look)."""
    from .studio import led_frame
    n = SC.loop_len(key, feed, size)
    now = time.time() if now is None else now
    frames = [led_frame(SC.render(key, feed, i, now, size=size), scale).convert("RGB") for i in range(n)]
    pal = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT) for f in frames[:1]][0]
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    q[0].save(buf, format="GIF", save_all=True, append_images=q[1:], duration=1000, loop=0,
              disposal=1, optimize=False)
    return buf.getvalue()


def install(app, sign, auto=None, feeds=None) -> DataLive:
    """Register /api/livedata/* routes. Called from web.create_app."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    live = DataLive(sign, auto, persist=True, feeds=feeds)   # feeds shared across displays
    app.state.livedata_live = live
    gif_cache: dict = {}

    def feed_or_404(key: str):
        if key not in SC.SCREENS:
            raise HTTPException(404, f"unknown screen {key}")
        return live.feeds[key]

    async def warm(keys):
        """Previews: fetch only what is due (Feed.poll_s), never forced, bounded wait."""
        fs = [live.feeds[k] for k in keys]
        try:
            await asyncio.wait_for(asyncio.gather(*(f.refresh() for f in fs), return_exceptions=True), 20)
        except (TimeoutError, asyncio.TimeoutError):
            pass

    def payload():
        return {"live": live.info(), "screens": screen_list(live)}

    @app.get("/api/livedata/screens")
    async def ld_screens(warm_all: bool = False):
        if warm_all:
            await warm(list(SC.SCREENS))
        return payload()

    @app.get("/api/livedata/status")
    async def ld_status():
        return {"live": live.info()}

    @app.get("/api/livedata/screens/{key}/frame.png")
    async def ld_frame(key: str, scale: int = 8, k: int = 0):
        f = feed_or_404(key)
        await warm([key])
        img = SC.render(key, f, k, size=live.size())
        return Response(png_bytes(img, max(1, min(scale, 16))), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/livedata/screens/{key}/led.png")
    async def ld_led_png(key: str, scale: int = 8, k: int = 0):
        from .studio import led_frame
        f = feed_or_404(key)
        await warm([key])
        buf = io.BytesIO()
        led_frame(SC.render(key, f, k, size=live.size()), max(2, min(scale, 16))).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/livedata/screens/{key}/led.gif")
    async def ld_led_gif(key: str, scale: int = 4):
        f = feed_or_404(key)
        await warm([key])
        scale = max(2, min(scale, 12))
        # time-dependent screens (clock, ISS countdown) re-render each minute
        ck = (key, f.ok_at, f.stale, scale, int(time.time() // 60), live.size())
        data = gif_cache.get(ck)
        if data is None:
            data = await asyncio.to_thread(led_gif, key, f, scale, None, live.size())
            if len(gif_cache) > 64:
                gif_cache.clear()
            gif_cache[ck] = data
        return Response(data, media_type="image/gif", headers={"Cache-Control": "no-store"})

    @app.post("/api/livedata/live")
    async def ld_live(r: DataLiveReq):
        try:
            if r.action == "start":
                await live.start(r.screens, r.rotate, r.rotate_s, r.interval)
            elif r.action == "stop":
                restored = await live.stop(restore=True)
                live.stopped_reason = "stopped by user" + (f"; sign on {restored}" if restored else "")
            else:
                raise HTTPException(400, "action must be start or stop")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return payload()

    @app.post("/api/livedata/test-frame")
    async def ld_test_frame(r: TestFrameReq):
        try:
            out = await live.send_test_frame(r.screen)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")
        return {**out, **payload()}

    @app.get("/api/livedata/feed-config")
    async def ld_feed_config():
        return feed_config()

    @app.post("/api/livedata/feed-config")
    async def ld_feed_config_set(r: FeedCfgReq):
        """Save symbols / currencies / RSS URLs; "" = back to the default. Feeds whose
        setting changed refetch once on their next tick (no forced burst)."""
        from .sources import ft8feed as FT
        from .sources import marketnews as MN
        from . import place as PLACE
        upd = {}
        try:
            for k, v in r.values.items():
                upd[k] = (PLACE.validate(k, v) if k in PLACE.CONFIG else FT.validate(k, v) if k in FT.CONFIG
                          else MN.validate(k, v))
        except ValueError as e:
            raise HTTPException(400, str(e))
        S.save(upd)
        touched = set(upd)
        ft8_touched = bool(touched & set(FT.CONFIG)) or bool(touched & {"home_lat", "home_lon"})
        place_touched = bool(touched & set(PLACE.CONFIG))
        if ft8_touched:
            FT.SOURCE.http_off_until = 0.0                  # new URL / source: ask HTTP again
            FT.SOURCE.polled_at = 0.0
        for f in live.feeds.values():
            keys = {getattr(f, "setting", None)} | ({"livedata_x_user", "livedata_x_bearer_token"} if f.key == "x_feed" else set())
            if (touched & keys or (ft8_touched and isinstance(f, FT.Ft8Feed))
                    or (place_touched and f.key in PLACE.PLACE_FEEDS)):
                f.attempt_at, f.error = None, None          # due now; old value kept until it answers
        return {**feed_config(), **payload()}

    @app.post("/api/livedata/config")
    async def ld_config(r: DataCfgReq):
        from .orcsdr_live import TRANSPORTS
        if r.transport is not None:
            if r.transport not in TRANSPORTS:
                raise HTTPException(400, "transport must be one of " + ", ".join(TRANSPORTS))
            live.transport = r.transport
        try:
            live.set_selection(r.screens, r.rotate, r.rotate_s, r.interval)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if live.enabled:
            live._save_armed(True)
        else:
            S.save(live._cfg())
        return payload()

    return live


def _safe_url(f) -> str:
    try:
        return str(f.url)
    except Exception as e:  # noqa: BLE001  (place not configured yet)
        return f"(not configured: {e})"
