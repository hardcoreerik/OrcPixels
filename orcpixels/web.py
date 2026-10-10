"""Local web UI + JSON API for the sign.  Run: python -m orcpixels serve  -> http://127.0.0.1:8765/

Owns a single SignClient (one BLE connection). Phase 2 plugs a frame provider
into AUTO (see AutoStatus.set_provider) and the UI's "Auto status" toggle
starts/stops it.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from . import protocol as P
from .ble_sign import SignClient
from .render import compose_status, preview_native_text, test_pattern

log = logging.getLogger("orcpixels.web")
STATIC = Path(__file__).resolve().parent / "static"

FrameProvider = Callable[[], Awaitable[Optional[Image.Image]]]


class AutoStatus:
    """Periodically asks a provider for a frame and shows it when it changes."""

    def __init__(self, sign: SignClient, interval: float = 2.0):
        self.sign, self.interval = sign, interval
        self.provider: FrameProvider | None = None
        self.provider_name: str | None = None
        self._task: asyncio.Task | None = None
        self._last: bytes | None = None
        self.last_error: str | None = None

    def set_provider(self, name: str, provider: FrameProvider):
        self.provider_name, self.provider = name, provider

    @property
    def enabled(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start(self):
        if not self.provider:
            raise RuntimeError("no auto-status provider installed yet (phase 2: window watcher)")
        if not self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self):
        while True:
            try:
                img = await self.provider()  # type: ignore[misc]
                if img is not None:
                    key = img.tobytes()
                    if key != self._last:
                        await self.sign.send_image(img)
                        self._last = key
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("auto status: %s", self.last_error)
            await asyncio.sleep(self.interval)


class TextReq(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    color: str = "ffffff"
    bg_color: Optional[str] = None
    animation: int = Field(0, ge=0, le=7)
    speed: int = Field(80, ge=0, le=100)
    mode: str = "native"          # native | image
    subtitle: str = ""


class LevelReq(BaseModel):
    level: int = Field(ge=0, le=100)


class PowerReq(BaseModel):
    on: bool


class AutoReq(BaseModel):
    enabled: bool


class SettingsReq(BaseModel):
    # module level: with `from __future__ import annotations` FastAPI can't resolve a
    # model defined inside create_app, so POST /api/settings used to answer 422
    rotate_180: Optional[bool] = None
    sign_stuck_timeout_s: Optional[float] = Field(None, ge=5, le=600)
    sign_idle_action: Optional[str] = Field(None, pattern="^(play|disconnect)$")


class UpdateSettingsReq(BaseModel):
    update_check: Optional[bool] = None
    update_channel: Optional[str] = Field(None, pattern="^(stable|beta)$")


def _png(img: Image.Image, scale: int = 1) -> Response:
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def _hex(c: Optional[str]) -> Optional[str]:
    return c.lstrip("#") if c else None


def create_app(address: str | None = None, display: dict | None = None,
               feeds: dict | None = None) -> FastAPI:
    """One display's full stack (SignClient, Auto status, Live engines, playlists, ...).
    display=None is the classic single-sign app (tests, CLI); create_multi_app() builds
    one of these per registered display and routes requests between them."""
    from . import displays as D
    if display is not None and not D.is_ipixel(display):
        from .drivers.sign import DriverSign
        sign = DriverSign(display)
    elif display is not None:
        sign = SignClient(address or display["address"], display=display)
    else:
        sign = SignClient(address) if address is not None else SignClient()
    auto = AutoStatus(sign)

    async def resume_after_reconnect():
        """SignClient keeper hook (link came back, e.g. sign power-cycled): Live resumes by
        itself if armed; Auto status re-sends; otherwise re-show the last playlist/preset."""
        for attr in ("orcsdr_live", "livedata_live"):
            live = getattr(app.state, attr, None)
            if live is not None and await live.on_sign_reconnect():
                return
        if auto.enabled:
            ai = getattr(app.state, "ai_status", None)
            if ai is not None:
                ai.reset()
            auto._last = None
            return
        hint = sign.resume_hint
        if not hint:
            return
        if hint.get("kind") == "playlist":
            from . import playlist as PL
            await PL.play(sign)
        elif hint.get("kind") == "preset":
            from . import presets as PR_
            await PR_.play(sign, hint["name"])
        log.info("resumed %s %s after reconnect", hint.get("kind"), hint.get("name"))

    async def resume_when_idle() -> str:
        """30 s rule (SignClient keeper): connected but nothing on the sign -> put the saved
        on-device playlist back (0x8008), else the OrcSDR default preset."""
        for attr in ("orcsdr_live", "livedata_live"):
            live = getattr(app.state, attr, None)
            if live is not None and live.enabled and live.state == "waiting":
                await live._do_fallback()
                return live.showing or "live fallback"
        from . import playlist as PL
        if PL.slot_record().get("active"):
            r = await PL.play(sign)
            return f"playlist {r['playlist']}"
        from . import presets as PR_
        name = B.default_preset("OrcSDR") or "orcsdr_wave_small_anim"
        await PR_.play(sign, name)
        return f"preset {name}"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from . import settings as S_
        autoconnect = display is None or (bool(display.get("autoconnect", True)) and D.configured(display))
        if autoconnect:
            try:
                await sign.connect()
            except Exception as e:  # noqa: BLE001  (the keeper keeps retrying)
                log.warning("initial connect failed: %s", e)
        else:
            # registered but not wanted: no link until the user presses Connect for it
            sign.want_connected = False
            sign.link_state = "disconnected"
            log.info("display %s (%s) not set to autoconnect; staying off", display["id"],
                     display["address"])
        sign.on_reconnect.append(resume_after_reconnect)
        sign.on_idle = resume_when_idle
        sign.follow_settings = True
        sign.start_supervisor()
        live = app.state.orcsdr_live
        if not autoconnect:
            pass
        elif S_.load().get("orcsdr_live_armed"):
            log.info("OrcSDR Live was armed before the restart; re-arming")
            try:
                await live.start()
            except Exception as e:  # noqa: BLE001
                log.warning("re-arming OrcSDR Live failed: %s", e)
        elif S_.load().get("livedata_armed"):
            log.info("Live Data was armed before the restart; re-arming")
            try:
                await app.state.livedata_live.start()
            except Exception as e:  # noqa: BLE001
                log.warning("re-arming Live Data failed: %s", e)
        yield
        await auto.stop()
        await sign.stop_supervisor()
        await sign.disconnect()

    app = FastAPI(title="OrcPixels", lifespan=lifespan)
    app.state.sign, app.state.auto = sign, auto
    app.state.display = display
    from .orcsdr_live import install as _install_orcsdr   # OrcSDR Live (read-only source)
    _install_orcsdr(app, sign, auto)
    from .livedata import install as _install_livedata   # Live Data screens (free web APIs)
    _install_livedata(app, sign, auto, feeds=feeds)
    app.state.orcsdr_live.peers = [app.state.livedata_live]   # one live source at a time
    app.state.livedata_live.peers = [app.state.orcsdr_live]
    from .ai_status import install as _install_ai     # AI status + buddy (the Auto status provider)
    _install_ai(app, sign, auto)
    from .playlist import install as _install_playlist   # on-device playlists (slots + 0x8008)
    _install_playlist(app, sign, auto)
    from .studio import install as _install_studio       # Image Converter / Image Creator
    _install_studio(app, sign, auto)
    from .activity import labelled

    from .ble_sign import Superseded

    async def guard(coro):
        try:
            return await coro
        except HTTPException:
            raise
        except Superseded as e:
            raise HTTPException(409, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        brief = json.dumps(_display_brief()).replace("</", "<\\/")
        resp = HTMLResponse(html.replace("<!--DISPLAY-->", f"<script>window.OP_DISPLAY={brief};</script>", 1))
        if display is not None:
            # the page's own /api calls must reach this display (e.g. opened as /d/<id>/)
            resp.set_cookie("opdisplay", display["id"], max_age=365 * 86400, samesite="lax")
        return resp

    @app.get("/api/status")
    async def status():
        s = sign.state
        return {"connected": sign.connected, "address": s.address, "name": s.name,
                "display": _display_brief(),
                "width": s.width, "height": s.height, "device_info": s.device_info,
                "brightness": s.brightness, "power": s.power, "last_error": s.last_error,
                "notifications": list(s.notifications)[-10:], "pid": os.getpid(),
                "animated": sign.last_gif is not None, "rotate_180": sign.rotate_180,
                "link": sign.link_info(),
                "auto": {"enabled": auto.enabled, "provider": auto.provider_name,
                         "available": auto.provider is not None, "last_error": auto.last_error}}

    def _display_brief() -> dict:
        d = display or {"id": None, "label": "Sign", "address": sign.address,
                        "width": sign.state.width, "height": sign.state.height}
        return {"id": d["id"], "label": d.get("label"), "address": d.get("address"),
                "width": int(d.get("width") or sign.state.width),
                "height": int(d.get("height") or sign.state.height)}

    def _remember_autoconnect(on: bool):
        if display is None:
            return
        display["autoconnect"] = on
        try:
            D.update(display["id"], autoconnect=on)
        except Exception as e:  # noqa: BLE001
            log.warning("could not save autoconnect for %s: %s", display["id"], e)

    # ------------------------------------------------ dashboard (status panel, ~1 s poll)
    def now_showing() -> dict:
        from . import playlist as PL
        live = active_live()
        sh = dict(sign.showing or {})
        out = {"kind": sh.get("kind") or "unknown", "name": sh.get("name"),
               "since": sh.get("since"), "slots": sh.get("slots"), "detail": sh.get("detail"),
               "source": "sign"}
        if live is not None and live.enabled:
            if live.state == "streaming":
                out.update(kind="live", name=live.showing_name(), detail=live.label(), source="live")
            else:
                out.update(kind="live-fallback", name=live.showing or out["name"],
                           detail=live.label(), source="live")
        elif auto.enabled:
            out.update(kind="auto", source="auto",
                       name=out["name"] if out["kind"] == "auto" else auto.provider_name,
                       detail=out["detail"] or "Auto status")
        act = PL.slot_record().get("active")
        if out["kind"] == "unknown" and act:      # nothing sent since the server started
            out.update(kind="playlist?", name=act.get("playlist"), slots=act.get("slots"),
                       detail="nothing sent since the server started; last play order set by "
                              "OrcPixels (" + str(act.get("played_at") or act.get("set_at")) + ")")
        if out["kind"] in ("playlist", "playlist?") and act:
            out["name"] = out["name"] or act.get("playlist")
            out["presets"] = act.get("presets")
        if not sign.connected:
            out["offline"] = ("sign not connected: it keeps showing its own stored playlist "
                              "or whatever it had last") 
        out["thumb"] = "/api/frame.gif" if sign.last_gif else "/api/frame.png"
        if sign.last_frame is None and out.get("presets"):   # playlist replayed, nothing rendered here
            out["thumb"] = f"/api/presets/{out['presets'][0]}/led?scale=4"
        out["active_playlist"] = act
        return out

    def files_rev() -> float:
        from . import playlist as PL
        rev = 0.0
        for p in (PL.playlists_path(), PL.slots_path(), PR.dir_for()):
            try:
                rev = max(rev, p.stat().st_mtime)
            except OSError:
                pass
        return rev

    def live_engines() -> list:
        return [x for x in (getattr(app.state, "orcsdr_live", None),
                            getattr(app.state, "livedata_live", None)) if x is not None]

    def active_live():
        """The enabled live engine (OrcSDR Live / Live Data; at most one), else None."""
        return next((x for x in live_engines() if x.enabled), None)

    def live_brief() -> dict | None:
        engines = live_engines()
        if not engines:
            return None
        live = active_live() or max(engines, key=lambda x: x.stopped_at or 0)
        return {"enabled": live.enabled, "state": live.state, "label": live.label(),
                "stopped_reason": live.stopped_reason, "stopped_at": live.stopped_at,
                "started_at": live.started_wall, "frames_sent": live.frames_sent,
                "source": "livedata" if live is getattr(app.state, "livedata_live", None) else "orcsdr",
                "name": live.NAME}

    def busy_with(what: str) -> bool:
        return any(e["what"] == what and e["status"] in ("queued", "sending")
                   for e in sign.activity.recent(50))

    @app.get("/api/now")
    async def api_now():
        return now_showing()

    @app.get("/api/activity")
    async def api_activity(since: int = 0, n: int = 20):
        return {"rev": sign.activity.rev, "items": sign.activity.since(since) if since
                else sign.activity.recent(max(1, min(n, 200)))}

    @app.get("/api/dash")
    async def api_dash(since: int = 0):
        return {"t": time.time(), "pid": os.getpid(), "connected": sign.connected,
                "name": sign.state.name, "link": sign.link_info(), "now": now_showing(),
                "display": _display_brief(),
                "rev": sign.activity.rev, "queue": sign.queue_info(),
                "live": live_brief(),
                "activity": sign.activity.since(since) if since else sign.activity.recent(20),
                "files_rev": files_rev(), "last_error": sign.state.last_error,
                "notifications": list(sign.state.notifications)[-10:],
                "rotate_180": sign.rotate_180, "animated": sign.last_gif is not None,
                "auto": {"enabled": auto.enabled, "provider": auto.provider_name,
                         "available": auto.provider is not None, "last_error": auto.last_error}}

    @app.post("/api/connect")
    async def connect():
        if display is not None and not sign.address:
            raise HTTPException(409, "no sign configured yet: Settings -> Scan for new displays -> Add display")
        sign.want_connected = True
        _remember_autoconnect(True)
        await guard(sign.connect())
        return await status()

    @app.post("/api/disconnect")
    async def disconnect():
        await sign.disconnect()          # explicit: the keeper stays off until /api/connect
        _remember_autoconnect(False)
        return await status()

    @app.post("/api/test/ble-drop")
    async def test_ble_drop(hard: bool = False):
        """Test hook: lose the BLE link like an unplugged sign (the keeper should reconnect
        and resume). hard=true abandons the link as if a GATT call hung."""
        await sign.drop_link_for_test(hard=hard)
        return await status()

    def _size() -> tuple[int, int]:
        return int(sign.state.width), int(sign.state.height)

    def _text_frame(r: TextReq) -> Image.Image:
        if r.mode == "image":
            fg = tuple(bytes.fromhex(_hex(r.color)))
            if _size() == (64, 64):
                from .render import compose_status64
                return compose_status64(r.text, r.subtitle, color=fg)
            return compose_status(r.text, r.subtitle, color=fg)
        return preview_native_text(r.text, _hex(r.color), _hex(r.bg_color), *_size())

    @app.post("/api/preview/text")
    async def preview_text(r: TextReq, scale: int = 8):
        return _png(_text_frame(r), scale)

    @app.post("/api/text")
    async def text(r: TextReq):
        with labelled(f"text '{r.text[:24]}'" + (" (image)" if r.mode == "image" else "")):
            if r.mode == "image":
                await guard(sign.send_image(_text_frame(r)))
                sign.set_showing("text", r.text[:40], detail="image mode")
            else:
                await guard(sign.send_text(r.text, color=_hex(r.color), bg_color=_hex(r.bg_color),
                                           animation=r.animation, speed=r.speed))
        return {"ok": True}

    @app.post("/api/image")
    async def image(file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(413, "image too large")
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"not an image: {e}")
        with labelled(f"image {file.filename or 'upload'}"[:60]):
            await guard(sign.send_image(img))
        return {"ok": True}

    @app.post("/api/test-image")
    async def test_image():
        with labelled("test pattern"):
            await guard(sign.send_image(test_pattern(*_size())))
        return {"ok": True}

    @app.post("/api/brightness")
    async def brightness(r: LevelReq):
        await guard(sign.brightness(r.level))
        return {"ok": True}

    @app.post("/api/power")
    async def power(r: PowerReq):
        await guard(sign.power(r.on))
        return {"ok": True}

    @app.post("/api/clear")
    async def clear():
        with labelled("blank (black frame)"):
            await guard(sign.clear())
        sign.set_showing("blank", "black frame")
        return {"ok": True}

    @app.post("/api/auto")
    async def set_auto(r: AutoReq):
        if r.enabled:
            for live in live_engines():                        # Auto status and the live
                if live.enabled:                               # modes are mutually exclusive
                    await live.stop()
                    live.stopped_reason = "auto status started"
            ai = getattr(app.state, "ai_status", None)
            if ai is not None and not auto.enabled:
                ai.reset()                                     # fresh start: send status now
            try:
                await auto.start()
            except RuntimeError as e:
                raise HTTPException(409, str(e))
        else:
            await auto.stop()
        return await status()

    # ------------------------------------------------ branding / splash
    from . import branding as B
    from . import presets as PR
    logo_cache: dict[int, bytes] = {}

    def _logo_png(size: int) -> Response:
        if size not in logo_cache:
            src = B.ROOT / "assets" / "orcpixels_logo.png"
            img = Image.open(src).convert("RGB") if src.exists() else Image.open(B.BRAND_ICON)
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            logo_cache[size] = buf.getvalue()
        return Response(logo_cache[size], media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

    @app.get("/favicon.ico")
    @app.get("/favicon.png")
    async def favicon():
        return _logo_png(64)

    @app.get("/logo.png")
    async def logo():
        return _logo_png(160)

    @app.post("/api/splash")
    async def splash():
        if _size() == (64, 64) and (PR.dir_for((64, 64)) / "orcpixels_splash.json").exists():
            meta = await guard(PR.play(sign, "orcpixels_splash"))
            sign.set_showing("splash", "OrcPixels splash")
            return {"ok": True, "preset": meta["name"]}
        gif, frames = B.splash_gif()
        with labelled("splash"):
            await guard(sign.send_gif(gif, frames[0]))
        sign.set_showing("splash", "OrcPixels splash")
        return {"ok": True, "frames": len(frames), "bytes": len(gif)}

    @app.get("/api/projects")
    async def list_projects():
        B.reload()
        return [{"key": b.key, "display": b.display, "accent": "#%02x%02x%02x" % b.accent[:3],
                 "has_icon": bool(b.icon and b.icon.exists())} for b in B.projects().values()]

    @app.get("/api/apps")
    async def list_apps():
        B.reload()
        return [{"key": b.key, "display": b.display, "has_icon": bool(b.icon and b.icon.exists()),
                 "process_names": b.process_names} for b in B.apps().values()]

    @app.get("/api/projects/{key}/default")
    async def project_default_file(key: str):
        name = B.default_preset(key)
        if not name:
            return _png(B.project_frame(key, "banner", _size()), 8)
        meta = PR.get(name)
        mt = "image/gif" if meta.get("kind") == "gif" else "image/png"
        return Response(PR.file_path(meta).read_bytes(), media_type=mt)

    @app.get("/api/projects/{key}/frame.png")
    async def project_preview(key: str, kind: str = "banner", scale: int = 8):
        try:
            return _png(B.project_frame(key, kind, _size()), max(1, min(scale, 16)))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/projects/{key}/show")
    async def project_show(key: str, kind: str = "default"):
        if kind == "default" and B.default_preset(key):
            meta = await guard(PR.play(sign, B.default_preset(key)))
            return {"ok": True, "preset": meta["name"]}
        kind = "banner" if kind == "default" else kind
        try:
            frame = B.project_frame(key, kind, _size())
        except KeyError as e:
            raise HTTPException(404, str(e))
        with labelled(f"project {key} {kind}"):
            await guard(sign.send_image(frame))
        sign.set_showing("project", f"{key} {kind}")
        return {"ok": True}

    # ------------------------------------------------ presets / settings
    from . import presets as PR
    from . import settings as S

    @app.get("/api/presets")
    async def presets_list():
        from . import adaptive as AD
        dd = D.current()
        pw, ph = int(dd["width"]), int(dd["height"])
        out = PR.list_presets()
        for m in out:
            m["rating"] = AD.rating_for(m["name"], pw, ph)
        return out

    # ---- adaptive layouts (docs/DESIGN.md): any preset at any size + compatibility rating
    def _adapt_args(name, w, h, strategy):
        from . import layout as LY
        if not (4 <= w <= 512 and 4 <= h <= 512):
            raise HTTPException(400, "size must be 4..512")
        if strategy not in LY.STRATEGIES:
            raise HTTPException(400, f"strategy: {', '.join(LY.STRATEGIES)}")

    _adapt_cache: dict = {}

    def _adapt_render(name, w, h, strategy):
        from . import adaptive as AD
        key = (name, w, h, strategy)
        if key not in _adapt_cache:
            try:
                _adapt_cache[key] = AD.render_preset(name, w, h, strategy)
            except KeyError as e:
                raise HTTPException(404, str(e))
            while len(_adapt_cache) > 48:
                _adapt_cache.pop(next(iter(_adapt_cache)))
        return _adapt_cache[key]

    @app.get("/api/adaptive/list")
    async def adaptive_list():
        from . import adaptive as AD
        dd = D.current()
        pw, ph = int(dd["width"]), int(dd["height"])
        return {"panel": [pw, ph], "items": [{**n, "rating": AD.rating_for(n["name"], pw, ph)} for n in AD.names()]}

    @app.get("/api/adaptive/info")
    async def adaptive_info(name: str, w: int, h: int, strategy: str = "adaptive"):
        from . import layout as LY
        _adapt_args(name, w, h, strategy)
        fr, durs, rating = await asyncio.to_thread(_adapt_render, name, w, h, strategy)
        return {**LY.Ctx(w, h).describe(), "name": name, "strategy": strategy, "rating": rating,
                "frames": len(fr), "loop_ms": sum(durs)}

    @app.get("/api/adaptive/render.gif")
    async def adaptive_render(name: str, w: int, h: int, strategy: str = "adaptive", scale: int = 0):
        from . import layout as LY
        from .render import encode_gif
        _adapt_args(name, w, h, strategy)
        fr, durs, rating = await asyncio.to_thread(_adapt_render, name, w, h, strategy)
        sc = scale or max(2, min(8, 384 // max(w, h)))
        step = max(1, len(fr) // 120)                       # preview: at most ~120 frames
        led = await asyncio.to_thread(lambda: [LY.led_preview(f, sc) for f in fr[::step]])
        data = encode_gif(led, [d * step for d in durs[::step]]) if len(led) > 1 else None
        if data is None:
            buf = io.BytesIO()
            led[0].save(buf, format="PNG")
            return Response(buf.getvalue(), media_type="image/png", headers={"X-Rating": rating})
        return Response(data, media_type="image/gif", headers={"X-Rating": rating, "Cache-Control": "max-age=300"})

    @app.post("/api/adaptive/{name}/show")
    async def adaptive_show(name: str, strategy: str = "adaptive"):
        """Render for this display's panel and show it (normal send path)."""
        from .activity import labelled
        from .render import encode_gif
        dd = D.current()
        pw, ph = int(dd["width"]), int(dd["height"])
        fr, durs, rating = await asyncio.to_thread(_adapt_render, name, pw, ph, strategy)
        if D.is_ipixel(dd):                                  # legacy transport: keep uploads small
            from .layout import reduce_frames
            fr, durs = reduce_frames(fr, durs, 48)
        with labelled(f"adaptive {name}"):
            if len(fr) > 1:
                await sign.send_gif(encode_gif(fr, durs))
            else:
                await sign.send_image(fr[0])
        return {"name": name, "size": [pw, ph], "rating": rating}

    @app.get("/api/presets/{name}/preview.png")
    async def preset_preview(name: str, scale: int = 8):
        try:
            return _png(PR.preview_image(name), max(1, min(scale, 16)))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/presets/{name}/file")
    async def preset_file(name: str):
        try:
            meta = PR.get(name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        mt = "image/gif" if meta.get("kind") == "gif" else "image/png"
        return Response(PR.file_path(meta).read_bytes(), media_type=mt)

    @app.post("/api/presets/{name}/show")
    async def preset_show(name: str):
        try:
            PR.get(name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        if busy_with(f"preset {name}"):     # double clicks used to queue the same 10 KB GIF 3x
            raise HTTPException(409, f"preset {name} is already being sent")
        meta = await guard(PR.play(sign, name))
        return {"ok": True, "preset": meta["name"]}

    @app.get("/api/settings")
    async def settings_get():
        return S.load()

    @app.post("/api/settings")
    async def settings_set(r: SettingsReq):
        return S.save({k: v for k, v in r.model_dump().items() if v is not None})

    @app.get("/api/frame.gif")
    async def frame_gif():
        if not sign.last_gif:
            raise HTTPException(404, "last frame was not animated")
        return Response(sign.last_gif, media_type="image/gif", headers={"Cache-Control": "no-store"})

    @app.get("/api/device/frame.png")
    async def device_frame(scale: int = 8):
        """What the panel itself shows (driver output after auto-fit / rotation); iPixel and
        anything not sent yet fall back to the last frame."""
        raw = sign.device_png() if hasattr(sign, "device_png") else None
        img = Image.open(io.BytesIO(raw)) if raw else (sign.last_frame or Image.new("RGB", (sign.state.width,
                                                                                            sign.state.height)))
        s = max(1, min(int(scale), 16))
        img = img.convert("RGB").resize((img.width * s, img.height * s), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/frame.png")
    async def frame(scale: int = 8):
        img = sign.last_frame or Image.new("RGB", (sign.state.width, sign.state.height))
        return _png(img, max(1, min(scale, 16)))

    return app


# ======================================================================= multi-display
COOKIE = "opdisplay"



# /api/displays request bodies at module level (FastAPI cannot resolve models defined
# inside a function under `from __future__ import annotations`: they become query params)
class SelectReq(BaseModel):
    id: str


class AddReq(BaseModel):
    address: str = ""
    driver: Optional[str] = None
    profile: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    serial: Optional[str] = None
    options: Optional[dict] = None
    name: Optional[str] = None
    label: Optional[str] = None
    width: Optional[int] = None              # None: from the profile (iPixel default 64x16)
    height: Optional[int] = None
    rotate_180: Optional[bool] = None
    id: Optional[str] = None


class EditReq(BaseModel):
    label: Optional[str] = None
    rotate_180: Optional[bool] = None
    follow_rotate: Optional[bool] = None     # True -> rotate_180 = null (global setting)
    width: Optional[int] = None
    height: Optional[int] = None
    host: Optional[str] = None
    port: Optional[int] = None
    serial: Optional[str] = None
    options: Optional[dict] = None



class MultiDisplayApp:
    """ASGI front for several displays (2026-10-08): one create_app() stack per registered
    display, each with its own SignClient (both panels can be driven at once; WinRT keeps
    separate GATT links), its own Live engines, playlists and per-display settings.

    Which stack a request goes to: /d/<id>/... prefix, X-Display header, ?display=<id>,
    the "opdisplay" cookie (set by POST /api/displays/select), else the primary display.
    /api/displays* is answered here (registry + state of every display)."""

    def __init__(self, address: str | None = None):
        from contextlib import AsyncExitStack

        from . import displays as D
        from . import livedata_screens as SC
        self.D = D
        self.feeds = SC.make_feeds()                 # one set of feed pollers for all displays
        self.displays: dict[str, dict] = {}
        self.apps: dict[str, FastAPI] = {}
        regs = D.load()
        if address and address.upper() != regs[0]["address"]:
            regs[0] = {**regs[0], "address": address.upper()}   # CLI --address override
        self.primary = regs[0]["id"]
        for d in regs:
            self._build(d)
        self._stack: AsyncExitStack | None = None
        self.root = self._root_app()

    def _build(self, d: dict):
        d = dict(d)
        self.displays[d["id"]] = d
        with self.D.use(d):
            self.apps[d["id"]] = create_app(d["address"], display=d, feeds=self.feeds)

    async def _enter(self, did: str):
        sub, d = self.apps[did], self.displays[did]
        tok = self.D.CURRENT.set(d)                  # tasks started in the lifespan inherit it
        try:
            await self._stack.enter_async_context(sub.router.lifespan_context(sub))
        finally:
            self.D.CURRENT.reset(tok)

    # -------------------------------------------------------------- routing
    @staticmethod
    def _cookie(scope) -> str | None:
        for k, v in scope.get("headers") or []:
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    name, _, val = part.strip().partition("=")
                    if name == COOKIE:
                        return val
        return None

    def pick(self, scope) -> tuple[str, dict]:
        path = scope.get("path", "")
        if path.startswith("/d/"):
            did, _, rest = path[3:].partition("/")
            if did in self.apps:
                scope = dict(scope, path="/" + rest, raw_path=("/" + rest).encode())
                return did, scope
        for k, v in scope.get("headers") or []:
            if k == b"x-display" and v.decode() in self.apps:
                return v.decode(), scope
        qs = (scope.get("query_string") or b"").decode()
        for part in qs.split("&"):
            name, _, val = part.partition("=")
            if name == "display" and val in self.apps:
                return val, scope
        c = self._cookie(scope)
        if c in self.apps:
            return c, scope
        return self.primary, scope

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan" or scope.get("path", "").startswith(("/api/displays", "/api/update")):
            return await self.root(scope, receive, send)
        did, scope = self.pick(scope)
        tok = self.D.CURRENT.set(self.displays[did])
        try:
            await self.apps[did](scope, receive, send)
        finally:
            self.D.CURRENT.reset(tok)

    # -------------------------------------------------------------- registry API
    def info(self, did: str, selected: str | None = None) -> dict:
        d, sub = self.displays[did], self.apps[did]
        sign = sub.state.sign
        sh = getattr(sign, "showing", None) or {}
        return {**{k: d.get(k) for k in ("id", "label", "name", "address", "width", "height",
                                          "rotate_180", "autoconnect", "driver", "profile", "host", "port",
                                          "serial")},
                "driver_status": getattr(getattr(sign, "driver", None), "status", "stable"),
                "render_size": [sign.state.width, sign.state.height],
                "primary": did == self.primary, "selected": did == selected,
                "connected": sign.connected, "link_state": getattr(sign, "link_state", None),
                "want_connected": getattr(sign, "want_connected", None),
                "device_size": [sign.state.width, sign.state.height] if sign.state.device_info else None,
                "showing": sh.get("name"), "showing_kind": sh.get("kind")}

    def _root_app(self) -> FastAPI:
        from contextlib import AsyncExitStack

        from fastapi.responses import JSONResponse
        D = self.D

        @asynccontextmanager
        async def lifespan(_app):
            from . import updater as UP
            self._stack = AsyncExitStack()
            upd = UP.start_background()                 # startup + 24 h update check (notice only)
            try:
                async with self._stack:
                    for did in list(self.apps):
                        await self._enter(did)
                    yield
            finally:
                upd.stop_event.set()

        root = FastAPI(title="OrcPixels displays", lifespan=lifespan)

        # ---- updates (docs/UPDATES.md): check / notice / explicit "Update now" only
        @root.get("/api/update")
        async def update_status():
            from . import updater as UP
            return UP.status()

        @root.post("/api/update/check")
        async def update_check():
            from . import updater as UP
            return await asyncio.to_thread(UP.check, True)

        @root.post("/api/update/settings")
        async def update_settings(r: UpdateSettingsReq):
            from . import settings as S
            from . import updater as UP
            ch = {}
            if r.update_check is not None:
                ch["update_check"] = bool(r.update_check)
            if r.update_channel is not None:
                ch["update_channel"] = r.update_channel
            tok = D.CURRENT.set(None)                  # shared setting, not per display
            try:
                S.save(ch)
            finally:
                D.CURRENT.reset(tok)
            return UP.status()

        @root.post("/api/update/install")
        async def update_install():
            from . import updater as UP
            try:
                return await asyncio.to_thread(UP.install, UP.request_shutdown)
            except PermissionError as e:
                raise HTTPException(403, str(e))
            except ValueError as e:
                raise HTTPException(409, str(e))
            except OSError as e:
                raise HTTPException(502, str(e))

        def selected(request: Request) -> str:
            c = request.cookies.get(COOKIE)
            return c if c in self.apps else self.primary

        @root.get("/api/displays")
        async def displays_list(request: Request):
            sel = selected(request)
            return {"selected": sel, "primary": self.primary,
                    "displays": [self.info(did, sel) for did in self.apps]}

        @root.post("/api/displays/select")
        async def displays_select(r: SelectReq, request: Request):
            if r.id not in self.apps:
                raise HTTPException(404, f"unknown display {r.id}")
            resp = JSONResponse({"selected": r.id,
                                 "displays": [self.info(did, r.id) for did in self.apps]})
            resp.set_cookie(COOKIE, r.id, max_age=3600 * 24 * 365, samesite="lax")
            return resp

        @root.get("/api/displays/profiles")
        async def profiles_list():
            from . import drivers as DR
            from . import profiles as PF
            return {"profiles": PF.brief(), "drivers": DR.STATUS}

        @root.post("/api/displays/scan")
        async def displays_scan(timeout: float = 6.0):
            """Explicit discovery (the only place a new panel is ever found): passive BLE
            scan for LED_BLE_* advertisers. Connected panels don't advertise."""
            from .ble_sign import scan
            try:
                found = await scan(timeout=max(2.0, min(timeout, 15.0)))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"scan failed: {type(e).__name__}: {e}")
            known = {d["address"]: did for did, d in self.displays.items()}
            return {"found": [{"name": dev.name, "address": (dev.address or "").upper(), "rssi": rssi,
                               "known_as": known.get((dev.address or "").upper())}
                              for dev, rssi in found]}

        @root.post("/api/displays/add")
        async def displays_add(r: AddReq):
            prim = self.displays.get(self.primary)
            try:
                d = D.add({**{k: v for k, v in r.model_dump().items() if v is not None}, "autoconnect": False})
            except ValueError as e:
                raise HTTPException(400, str(e))
            if prim is not None and d["id"] == self.primary:
                # fresh install: the first sign fills the unconfigured primary entry
                prim.update(d)
                sign = self.apps[self.primary].state.sign
                sign.address = sign.state.address = d["address"]
                sign.state.width, sign.state.height = d["width"], d["height"]
                sign.state.name = d.get("name") or None
                return self.info(self.primary)
            self._build(d)
            if self._stack is not None:
                await self._enter(d["id"])
            return self.info(d["id"])

        @root.post("/api/displays/{did}")
        async def displays_edit(did: str, r: EditReq):
            if did not in self.apps:
                raise HTTPException(404, f"unknown display {did}")
            ch = {k: v for k, v in r.model_dump().items() if v is not None and k != "follow_rotate"}
            if r.follow_rotate:
                ch["rotate_180"] = None
            try:
                nd = D.update(did, **ch)
            except ValueError as e:
                raise HTTPException(400, str(e))
            self.displays[did].update(nd)             # same dict the stack's SignClient reads
            sign = self.apps[did].state.sign
            if hasattr(sign, "driver"):                 # host / port / options changed: new driver
                from . import drivers as DR
                from . import profiles as PF
                await sign._safe_disconnect()
                sign.driver = DR.make(PF.config_for(nd))
                sign.state.width, sign.state.height = D.render_size(nd)
                return self.info(did)
            if not sign.state.device_info:
                sign.state.width, sign.state.height = nd["width"], nd["height"]
            return self.info(did)

        @root.delete("/api/displays/{did}")
        async def displays_delete(did: str):
            if did not in self.apps:
                raise HTTPException(404, f"unknown display {did}")
            try:
                D.remove(did)
            except ValueError as e:
                raise HTTPException(400, str(e))
            sub = self.apps.pop(did)
            self.displays.pop(did)
            try:
                for attr in ("orcsdr_live", "livedata_live"):
                    live = getattr(sub.state, attr, None)
                    if live is not None and live.enabled:
                        await live.stop()
                await sub.state.auto.stop()
                await sub.state.sign.stop_supervisor()
                await sub.state.sign.disconnect()
            except Exception as e:  # noqa: BLE001
                log.warning("stopping removed display %s: %s", did, e)
            return {"removed": did}

        return root


def create_multi_app(address: str | None = None) -> MultiDisplayApp:
    return MultiDisplayApp(address)


class LanGuard:
    """ASGI wrapper: when settings.lan_token is set, requests from non-loopback clients must
    carry it (X-OrcPixels-Token header, ?token=, or the opt cookie set by ?token=)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = (scope.get("client") or ("127.0.0.1", 0))[0]
            if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
                from . import settings as S
                tok = str(S.load().get("lan_token") or "")
                if tok:
                    import hmac
                    import urllib.parse
                    hdr = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
                    q = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
                    cookie = hdr.get("cookie", "")
                    got = hdr.get("x-orcpixels-token") or (q.get("token") or [""])[0] or \
                        next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("optoken=")), "")
                    if not hmac.compare_digest(got, tok):
                        if scope["type"] == "http":
                            await send({"type": "http.response.start", "status": 401,
                                        "headers": [(b"content-type", b"text/plain")]})
                            await send({"type": "http.response.body", "body": b"OrcPixels: LAN token required"})
                        return
        await self.app(scope, receive, send)


def serve(host: str = "127.0.0.1", port: int = 8765, address: str | None = None):
    import copy

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG
    cfg = copy.deepcopy(LOGGING_CONFIG)          # timestamps in server.log (access log)
    cfg["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    cfg["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    uvicorn.run(LanGuard(create_multi_app(address)), host=host, port=port, log_level="info", log_config=cfg)
