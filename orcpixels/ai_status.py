"""AI status + buddy mode: follow the foreground coding assistant and show
  * a STATUS frame: app icon + project (branding accent) / App/branch / worktree
  * BUDDY lines: what the agent is doing right now ('editing web.py'), with a tiny
    state icon, alternating with the status frame.

Sources: orcpixels.sources.ai_watcher (foreground window + session state + git) and
orcpixels.sources.buddy (session-log tails). Everything is read-only.

BLE budget (~1.1 KB/s): the status frame is a static PNG (~300-500 B) unless a line
overflows, then a short GIF that pages through the long line (~400 B per page). Buddy lines use the
sign's NATIVE scrolling text (the device scrolls it; ~20 B/char, the state icon is
a custom 16x16 glyph), so a 40-char line costs ~0.9 KB. Sends only happen when
something changes: status debounced 3 s, buddy at most every 12 s, then the status
frame is restored after the line has scrolled by.

Web: install(app, sign, auto) plugs the controller into web.AutoStatus (the
'Auto status' toggle) and adds /api/ai/*. CLI: python -m orcpixels watch [--buddy] [--dry-run]
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Callable, Optional

from PIL import Image, ImageDraw

from . import activity as A_
from . import protocol as P
from . import settings as S
from .sources import ai_watcher as AW
from .sources import buddy as BD
from .tinyfont import _G as _TINY_BASE

log = logging.getLogger("orcpixels.ai_status")
W, H = 64, 16
SETTING_BUDDY = "ai_buddy"
SETTING_ENABLED = "ai_enabled"         # master switch for the AI status / buddy feature

STATUS_DEBOUNCE_S = 3.0
BUDDY_MIN_GAP_S = 12.0
BUDDY_AFTER_STATUS_S = 8.0
BUDDY_SPEED = 85           # device native scroll speed 0..100
PAGE_MS = 1600

# ------------------------------------------------------------ tiny 3x5 font
_EXTRA = {
    "_": "000000000000111", "@": "010101111100011", "\\": "100100010001001",
    "(": "010100100100010", ")": "010001001001010", ",": "000000000010100",
    "'": "010010000000000", "=": "000111000111000", "#": "101111101111101",
    "!": "010010010000010", "*": "101010101000000", "&": "010101010101011",
    "<": "001010100010001", ">": "100010001010100", "~": "000011110000000",
    "[": "110100100100110", "]": "011001001001011", "…": "000000000000101",
}
_TINY = {**_TINY_BASE, **_EXTRA}
_NARROW = {".": 2, ":": 2, " ": 2, ",": 2, "'": 2, "!": 2}


def tiny_width(text: str) -> int:
    return max(0, sum(_NARROW.get(c, 4) for c in text.upper()) - 1)


def tiny_draw(img: Image.Image, x: int, y: int, text: str, color) -> int:
    px = img.load()
    iw, ih = img.size
    for ch in text.upper():
        g = _TINY.get(ch, _TINY["?"])
        narrow = ch in _NARROW
        for r in range(5):
            for c in range(3):
                if g[r * 3 + c] == "1":
                    xx, yy = x + (c - 1 if narrow else c), y + r
                    if 0 <= xx < iw and 0 <= yy < ih:
                        px[xx, yy] = color
        x += _NARROW.get(ch, 4)
    return x


# ------------------------------------------------------------ status frame
def _hex(c, default=(255, 255, 255)):
    try:
        return tuple(bytes.fromhex(c.lstrip("#"))) if c else default
    except ValueError:
        return default


def _app_brand(key: str) -> tuple[Optional[Image.Image], tuple, str]:
    from . import branding as B
    b = B.apps().get(key)
    icon = b.icon_image() if b else None
    if icon is None and B.BRAND_ICON.exists():
        icon = Image.open(B.BRAND_ICON).convert("RGBA")
    return icon, (b.accent if b else (200, 200, 200)), (b.display if b else key.title())


def _project_for(det: AW.Detection | None):
    from . import branding as B
    if det is None:
        return None, None
    if det.git:
        return B.find_project(det.git.repo), det.git.repo
    hint = (det.extra.get("project_hint") or "").lower()
    if hint:      # Grok Bot: no cwd; guess from the agent name + recent transcript text
        import re as _re
        best, n = None, 0
        for b in B.projects().values():
            c = len(_re.findall(r"\b%s\b" % _re.escape(b.key.lower()), hint))
            if c > n:
                best, n = b, c
        if best is not None:
            return best, best.display
    return None, None


def short_worktree(g: AW.GitInfo) -> str:
    """'C:\\src\\Proj-TEMP\\wt-vfo' -> 'Proj-TEMP/wt-vfo';
    '...\\.codex\\worktrees\\v3c-bw\\OrcSDR' -> 'v3c-bw/OrcSDR'."""
    parts = [p for p in g.toplevel.replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:])


def status_lines(det: AW.Detection | None) -> tuple[list[tuple[str, tuple]], Optional[Image.Image]]:
    """-> ([(text, color), ...] 2-3 lines, icon)."""
    if det is None:
        from . import branding as B
        icon = Image.open(B.BRAND_ICON).convert("RGBA") if B.BRAND_ICON.exists() else None
        return [("OrcPixels", (80, 255, 40)), ("no AI app", (120, 120, 120))], icon
    icon, app_col, app_name = _app_brand(det.app)
    proj, repo = _project_for(det)
    if proj is not None:
        title, tcol = proj.display, proj.accent[:3]
    elif repo:
        title, tcol = repo, (255, 255, 255)
    else:
        title, tcol = (BD.condense(det.session or "", 24) or "no project"), (150, 150, 150)
    branch = det.git.branch if det.git else ""
    lines = [(title, tcol), (f"{app_name}/{branch}" if branch else app_name, app_col[:3])]
    if det.git and det.git.is_worktree:
        lines.append(("WT " + short_worktree(det.git), (0, 170, 230)))
    return lines, icon


def _pages(text: str, area: int) -> list[str]:
    """Split a line into chunks that each fit `area` px, preferring to break after
    '/', '-', '_', ' ' (so 'Codex/claude/dsp-multirate' -> 'CODEX/', 'CLAUDE/DSP-', ...)."""
    if tiny_width(text) <= area:
        return [text]
    import re as _re
    toks = [t for t in _re.split(r"(?<=[/\\\-_ ])", text) if t]
    pages, cur = [], ""
    for t in toks:
        while tiny_width(t) > area:               # a single long token: hard split
            if cur:
                pages.append(cur)
                cur = ""
            n = len(t)
            while n > 1 and tiny_width(t[:n]) > area:
                n -= 1
            pages.append(t[:n])
            t = t[n:]
        if tiny_width(cur + t) <= area:
            cur += t
        else:
            pages.append(cur)
            cur = t
    if cur:
        pages.append(cur)
    return [p.strip() for p in pages if p.strip()]


def _wrap_tiny(text: str, area: int, lines: int) -> list[str]:
    out = _pages(text, area)
    return out[:lines]


def render_status64(det: AW.Detection | None) -> tuple[list[Image.Image], Optional[bytes]]:
    """64x64 matrix: app icon 2x (top-left), project title in big type, then app/branch
    and worktree lines wrapped (no pages needed: everything fits as one static PNG)."""
    from .live64 import U, header
    lines, icon = status_lines(det)
    img = Image.new("RGB", (64, 64))
    (title, tcol), rest = lines[0], lines[1:]
    acol = rest[0][1] if rest else (120, 120, 120)
    header(img, "AI STATUS", acol)
    if icon is not None:
        ic = icon.copy()
        ic.thumbnail((24, 24), Image.Resampling.NEAREST if max(icon.size) <= 16 else Image.Resampling.LANCZOS)
        if max(ic.size) < 24 and max(icon.size) <= 16:
            ic = icon.resize((icon.width * 24 // max(icon.size), icon.height * 24 // max(icon.size)),
                             Image.Resampling.NEAREST)
        img.paste(ic, (0, 11 + (24 - ic.height) // 2), ic if ic.mode == "RGBA" else None)
    from .livedata_screens import big_w
    tl = _wrap_tiny(title, 36, 3)
    if len(tl) == 1 and big_w(title) <= 36:
        U(img, 27, 14, title, tcol)
    else:
        for i, t in enumerate(tl):
            tiny_draw(img, 27, 12 + 7 * i, t, tcol)
    y = 38
    for t, col in rest:
        for part in _wrap_tiny(t, 64, 2):
            if y > 58:
                break
            tiny_draw(img, 0, y, part, col)
            y += 7
    return [img], None


def render_buddy64(act: BD.Activity) -> Image.Image:
    """Buddy on the 64x64 matrix: the device's native text mode is a single 16 px line,
    so this is an image: state glyph 2x + state word, the activity text wrapped below."""
    from .live64 import header
    col = _hex(STATE_COLORS.get(act.state))
    img = Image.new("RGB", (64, 64))
    header(img, act.state.upper(), col, "BUDDY", (120, 120, 120))
    g = _icon_glyph(act.state).resize((24, 24), Image.Resampling.NEAREST)
    img.paste(Image.new("RGB", (24, 24), col), (0, 11), g)
    txt_col = (255, 255, 255) if act.state != "done" else (200, 255, 176)
    # word wrap: 4 lines beside the glyph (38 px), then up to 4 full-width lines below
    import re as _re
    toks = [t for t in _re.split(r"(?<=[/\\\-_ ])", act.text) if t]
    lines, cur = [], ""
    def area():
        return 38 if len(lines) < 4 else 64
    for t in toks:
        while tiny_width(t.strip()) > area():          # one token wider than the line: hard split
            if cur.strip():
                lines.append(cur.strip())
                cur = ""
            n = len(t)
            while n > 1 and tiny_width(t[:n]) > area():
                n -= 1
            lines.append(t[:n])
            t = t[n:]
        if tiny_width((cur + t).strip()) <= area():
            cur += t
        else:
            lines.append(cur.strip())
            cur = t
    if cur.strip():
        lines.append(cur.strip())
    for i, t in enumerate(lines[:8]):
        if i < 4:
            tiny_draw(img, 26, 12 + 7 * i, t, txt_col)
        else:
            tiny_draw(img, 0, 38 + 7 * (i - 4), t, txt_col)
    return img


def render_status(det: AW.Detection | None, size: tuple[int, int] = (64, 16)) -> tuple[list[Image.Image], Optional[bytes]]:
    """-> (frames, gif_bytes|None). Everything fits -> one static PNG. A line that
    overflows is shown as pages (GIF, one frame per page, PAGE_MS each) instead of a
    pixel scroll: a smooth 64x16 scroll costs ~390 B/frame (~9 KB, ~8 s of BLE),
    pages cost ~400 B per page."""
    if tuple(size) == (64, 64):
        return render_status64(det)
    from .render import encode_gif
    lines, icon = status_lines(det)
    x0, area = 18, W - 18
    ys = [0, 6, 11] if len(lines) == 3 else [2, 9]
    paged = [_pages(t, area) for t, _ in lines]
    n = max(len(p) for p in paged)

    def frame(i: int) -> Image.Image:
        img = Image.new("RGB", (W, H))
        for (t, col), pages, y in zip(lines, paged, ys):
            tiny_draw(img, x0, y, pages[min(i, len(pages) - 1)] if len(pages) > 1 else t, col)
        if icon is not None:
            ic = icon.copy()
            ic.thumbnail((16, 16))
            img.paste(ic, (0, (H - ic.height) // 2), ic)
        # page dots under the icon area are not possible (icon is 16 rows); mark
        # continuation with a dim pixel at the right edge of paged lines
        for pages, y in zip(paged, ys):
            if len(pages) > 1 and i < len(pages) - 1:
                img.putpixel((W - 1, y + 4), (90, 90, 90))
        return img

    frames = [frame(i) for i in range(n)]
    if n == 1:
        return frames, None
    return frames, encode_gif(frames, PAGE_MS)


# ------------------------------------------------------------ buddy (native text)
_STATE_ICONS = {
    # 16x16 1-bit glyphs, drawn in the state colour by the device
    "thinking": ["................", ".....######.....", "...##......##...", "..#..........#..",
                 "..#..#..#..#.#..", "..#..........#..", "...##......##...", ".....######.....",
                 "......#.........", ".....#..........", "....##..........", "...##...........",
                 "..#.............", "................", "................", "................"],
    "reading": ["................", "................", "....#####.......", "...#.....#......",
                "..#.......#.....", "..#.......#.....", "..#.......#.....", "..#.......#.....",
                "...#.....#......", "....######......", "..........##....", "...........##...",
                "............##..", ".............#..", "................", "................"],
    "editing": ["................", "...........##...", "..........####..", ".........####...",
                "........####....", ".......####.....", "......####......", ".....####.......",
                "....####........", "...####.........", "...###..........", "..##............",
                "..#.............", "................", "................", "................"],
    "running": ["................", "................", "...##...........", "...####.........",
                "...######.......", "...########.....", "...##########...", "...###########..",
                "...##########...", "...########.....", "...######.......", "...####.........",
                "...##...........", "................", "................", "................"],
    "talking": ["................", "..############..", ".#............#.", ".#..#..#..#...#.",
                ".#............#.", ".#............#.", "..#####..######.", "......#.#.......",
                "......##........", ".......#........", "................", "................",
                "................", "................", "................", "................"],
    "done":    ["................", "................", "..............#.", ".............##.",
                "............##..", "...........##...", "..........##....", ".##......##.....",
                "..##....##......", "...##..##.......", "....####........", ".....##.........",
                "................", "................", "................", "................"],
}
STATE_COLORS = {"thinking": "b388ff", "reading": "40c4ff", "editing": "ffd740",
                "running": "69f0ae", "talking": "ffffff", "done": "8cfc28"}


def _icon_glyph(state: str) -> Image.Image:
    rows = _STATE_ICONS.get(state, _STATE_ICONS["thinking"])
    g = Image.new("L", (16, 16))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "#":
                g.putpixel((x, y), 255)
    return g


def buddy_command(act: BD.Activity, speed: int = BUDDY_SPEED) -> P.Command:
    """Native scrolling text: [state icon glyph][space][text], per-glyph colours."""
    font = P.load_font(size=16)
    col = bytes.fromhex(STATE_COLORS.get(act.state, "ffffff"))
    txt_col = bytes.fromhex("ffffff" if act.state != "done" else "c8ffb0")
    chars = bytearray()
    chars += bytes([0x01]) + col + P._reverse_bits(P._encode_glyph(_icon_glyph(act.state)))
    text = act.text
    for ch in text:
        glyph, w = P.render_glyph(ch, font, 16)
        chars += bytes([0x01 if w > 8 else 0x00]) + txt_col + P._reverse_bits(glyph)
    n = 1 + len(text)
    props = bytearray([0x00, 0x01, 0x01, int(P.TextAnimation.SCROLL_LEFT), speed & 0xFF, 0]) + txt_col
    props += b"\x00\x00\x00\x00"
    payload = bytes([n]) + bytes(props) + bytes(chars)
    return P._windowed("send_text", b"\x00\x01", payload, bytes([0x00, 0x00]))


def render_buddy_preview(act: BD.Activity, width: int = W) -> Image.Image:
    """Approximation of the buddy line as it appears once scrolled fully in
    (icon at the left edge). width > 64 renders the whole strip."""
    font = P.load_font(size=16)
    tw = 16 + sum(P.render_glyph(c, font, 16)[1] for c in act.text)
    img = Image.new("RGB", (max(width, 1) if width else tw, H))
    col = _hex(STATE_COLORS.get(act.state))
    img.paste(Image.new("RGB", (16, 16), col), (0, 0), _icon_glyph(act.state))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((16, 0), act.text, fill=(255, 255, 255) if act.state != "done" else (200, 255, 176), font=font)
    return img


def buddy_hold_s(act: BD.Activity) -> float:
    return max(8.0, min(18.0, 5.0 + 0.3 * len(act.text)))


# ------------------------------------------------------------ controller
class AIStatus:
    """Owns detection + send decisions. tick() is called ~1/s (by web.AutoStatus
    or the CLI loop). With sign=None (dry run) nothing is sent; `emit` gets a
    description of every would-be send."""

    def __init__(self, sign=None, buddy: bool | None = None, dry_run: bool = False,
                 emit: Callable[[str, dict], None] | None = None):
        self.sign = sign
        self.dry_run = dry_run or sign is None
        self.buddy = bool(S.load().get(SETTING_BUDDY, False)) if buddy is None else bool(buddy)
        self.emit = emit
        self.watcher = AW.Watcher()
        self.on_takeover: Callable[[], None] | None = None
        self.reset()

    def reset(self):
        self.detection: AW.Detection | None = None
        self.activity: BD.Activity | None = None
        self.detected_at = 0.0
        self._cand_key = object()
        self._cand_since = 0.0
        self._sent_key = object()
        self._sent_frame = None
        self.showing: str | None = None       # 'status' | 'buddy'
        self.status_sent_at = 0.0
        self.buddy_sent_at = -1e9
        self.buddy_hold = 0.0
        self.last_buddy: BD.Activity | None = None
        self._last_buddy_key = None
        self.last_send: dict | None = None
        self.sends = 0
        self.stopped_reason: str | None = None
        self.last_error: str | None = None

    # -- detection only (used by the UI when auto status is off)
    @staticmethod
    def enabled() -> bool:
        """Settings switch 'ai_enabled' (default on); off = never look at other apps."""
        return S.load().get(SETTING_ENABLED, True) is not False

    async def detect(self, max_age: float = 2.0) -> AW.Detection | None:
        if not self.enabled():
            self.detection, self.activity = None, None
            return None
        if time.monotonic() - self.detected_at > max_age:
            self.detection = await asyncio.to_thread(self.watcher.poll)
            self.detected_at = time.monotonic()
            self.activity = self.detection.extra.get("activity") if self.detection else None
        return self.detection

    async def tick(self):
        now = time.monotonic()
        # someone else (preset / text / OrcSDR Live / image) took over the sign -> stop
        if self.sign is not None and self._sent_frame is not None and self.sign.last_frame is not self._sent_frame:
            self.stopped_reason = "another display command took over the sign"
            self._sent_frame = None
            if self.on_takeover:
                self.on_takeover()
            return None
        det = await self.detect(max_age=0.5)
        key = det.key() if det else None
        if key != self._cand_key:
            self._cand_key, self._cand_since = key, now
        stable = now - self._cand_since >= STATUS_DEBOUNCE_S or self.sends == 0 and now - self._cand_since >= 1.0
        if stable and key != self._sent_key:
            await self._send_status(det, "change")
            self._sent_key = key
            return None
        if self.showing == "buddy" and now - self.buddy_sent_at >= self.buddy_hold:
            await self._send_status(det, "restore")
            return None
        act = self.activity
        if (self.buddy and act and self.showing == "status" and key == self._sent_key
                and (act.state, act.text) != self._last_buddy_key
                and now - self.buddy_sent_at >= BUDDY_MIN_GAP_S
                and now - self.status_sent_at >= BUDDY_AFTER_STATUS_S):
            await self._send_buddy(act)
        return None

    def size(self) -> tuple[int, int]:
        st = getattr(self.sign, "state", None)
        try:
            return (int(st.width), int(st.height)) if st is not None else (W, H)
        except Exception:  # noqa: BLE001
            return (W, H)

    async def _send_status(self, det, why: str):
        frames, gif = render_status(det, self.size())
        png = P.image_to_png(frames[0])
        info = {"kind": "status", "why": why, "animated": gif is not None,
                "bytes": len(gif) if gif else len(png), "frames": len(frames),
                "lines": [t for t, _ in status_lines(det)[0]],
                "detection": _det_summary(det)}
        if not self.dry_run:
            t0 = time.monotonic()
            with A_.labelled(f"auto status ({why})"):
                if gif:
                    await self.sign.send_gif(gif, frames[0])
                else:
                    await self.sign.send_image(frames[0])
            if hasattr(self.sign, "set_showing"):
                self.sign.set_showing("auto", "Auto status", detail=why)
            self._sent_frame = self.sign.last_frame
            info["send_ms"] = round((time.monotonic() - t0) * 1000)
        self._after_send(info)
        self.showing, self.status_sent_at = "status", time.monotonic()

    async def _send_buddy(self, act: BD.Activity):
        if self.size() == (64, 64):
            return await self._send_buddy64(act)
        cmd = buddy_command(act)
        size = sum(len(f.data) for f in cmd.frames)
        info = {"kind": "buddy", "state": act.state, "text": act.text, "bytes": size,
                "source": act.source}
        if not self.dry_run:
            t0 = time.monotonic()
            s = self.sign
            await s._ensure_orientation(2 if s.rotate_180 else 0)   # device lays out text glyphs
            with A_.labelled(f"buddy {act.state}: {act.text}"[:60]):
                await s.run(cmd)
            if hasattr(s, "set_showing"):
                s.set_showing("auto", "Buddy", detail=f"{act.state}: {act.text}")
            s.last_gif = None
            s.last_frame = render_buddy_preview(act)
            self._sent_frame = s.last_frame
            info["send_ms"] = round((time.monotonic() - t0) * 1000)
        self._after_send(info)
        self.last_buddy, self._last_buddy_key = act, (act.state, act.text)
        self.showing, self.buddy_sent_at, self.buddy_hold = "buddy", time.monotonic(), buddy_hold_s(act)

    async def _send_buddy64(self, act: BD.Activity):
        img = render_buddy64(act)
        info = {"kind": "buddy", "state": act.state, "text": act.text,
                "bytes": len(P.image_to_png(img, 64, 64)), "source": act.source, "image": True}
        if not self.dry_run:
            t0 = time.monotonic()
            with A_.labelled(f"buddy {act.state}: {act.text}"[:60]):
                await self.sign.send_image(img)
            if hasattr(self.sign, "set_showing"):
                self.sign.set_showing("auto", "Buddy", detail=f"{act.state}: {act.text}")
            self._sent_frame = self.sign.last_frame
            info["send_ms"] = round((time.monotonic() - t0) * 1000)
        self._after_send(info)
        self.last_buddy, self._last_buddy_key = act, (act.state, act.text)
        self.showing, self.buddy_sent_at, self.buddy_hold = "buddy", time.monotonic(), buddy_hold_s(act)

    def _after_send(self, info: dict):
        info["at"] = time.time()
        self.last_send = info
        self.sends += 1
        if self.emit:
            self.emit(info["kind"], info)

    def state(self) -> dict:
        det = self.detection
        return {"buddy": self.buddy, "dry_run": self.dry_run,
                "detection": _det_summary(det),
                "activity": self.activity.as_dict() if self.activity else None,
                "last_buddy": self.last_buddy.as_dict() if self.last_buddy else None,
                "showing": self.showing, "last_send": self.last_send, "sends": self.sends,
                "stopped_reason": self.stopped_reason}


def _det_summary(det: AW.Detection | None) -> dict | None:
    if det is None:
        return None
    g = det.git
    proj, repo = _project_for(det)
    return {"app": det.app, "app_display": det.app_display, "via": det.via,
            "project": proj.display if proj else repo, "repo": g.repo if g else None,
            "branch": g.branch if g else None,
            "worktree": g.toplevel if g and g.is_worktree else None,
            "toplevel": g.toplevel if g else None, "main_toplevel": g.main_toplevel if g else None,
            "folder": det.folder, "cwd": det.cwd, "session": det.session, "source": det.source,
            "window_title": det.fg_title}


# ------------------------------------------------------------------ web hook
from pydantic import BaseModel  # noqa: E402


class AICfgReq(BaseModel):
    buddy: Optional[bool] = None
    enabled: Optional[bool] = None


def install(app, sign, auto) -> AIStatus:
    """Plug into web.AutoStatus (the 'Auto status' toggle) + add /api/ai/* routes."""
    from fastapi.responses import Response

    ctl = AIStatus(sign)
    app.state.ai_status = ctl

    async def provider():
        await ctl.tick()
        return None            # the controller sends itself (PNG/GIF/native text)

    auto.set_provider("ai_watcher", provider)
    auto.interval = 1.0

    def _takeover():
        asyncio.get_running_loop().create_task(auto.stop())
    ctl.on_takeover = _takeover

    def _png(img, scale):
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/ai/state")
    async def ai_state():
        if not auto.enabled:
            try:
                await ctl.detect(max_age=2.0)
            except Exception as e:  # noqa: BLE001
                ctl.last_error = f"{type(e).__name__}: {e}"
        out = ctl.state()
        out["enabled"] = auto.enabled and auto.provider_name == "ai_watcher"
        out["feature_enabled"] = ctl.enabled()
        out["capabilities"] = AW.capabilities()
        out["error"] = auto.last_error or ctl.last_error
        return out

    @app.post("/api/ai/config")
    async def ai_config(r: AICfgReq):
        if r.enabled is not None:
            S.save({SETTING_ENABLED: bool(r.enabled)})
            if not r.enabled and auto.enabled and auto.provider_name == "ai_watcher":
                await auto.stop()
        if r.buddy is not None:
            ctl.buddy = bool(r.buddy)
            S.save({SETTING_BUDDY: ctl.buddy})
            if not ctl.buddy and ctl.showing == "buddy":
                ctl.buddy_hold = 0.0          # restore the status frame on the next tick
        return await ai_state()

    @app.get("/api/ai/preview.png")
    async def ai_preview(kind: str = "status", scale: int = 8, full: bool = False):
        await ctl.detect(max_age=2.0)
        scale = max(1, min(scale, 16))
        if kind == "buddy":
            act = ctl.activity or ctl.last_buddy or BD.Activity("thinking", "waiting for activity")
            if ctl.size() == (64, 64):
                return _png(render_buddy64(act), scale)
            return _png(render_buddy_preview(act, 0 if full else W), scale)
        frames, _ = render_status(ctl.detection, ctl.size())
        return _png(frames[0], scale)

    return ctl


# ------------------------------------------------------------------ CLI
def watch_main(a) -> int:
    """python -m orcpixels watch [--buddy] [--dry-run] [--seconds N] [--preview-dir D]"""
    import sys

    def emit(kind, info):
        ts = time.strftime("%H:%M:%S")
        if kind == "status":
            d = info["detection"] or {}
            print(f"{ts} SEND status ({'gif %d fr' % info['frames'] if info['animated'] else 'png'}, "
                  f"{info['bytes']} B, {info['why']}): {' | '.join(info['lines'])}"
                  f"   [app={d.get('app')} repo={d.get('repo')} branch={d.get('branch')} "
                  f"worktree={d.get('worktree')} folder={d.get('folder')}]", flush=True)
        else:
            print(f"{ts} SEND buddy  (native text, {info['bytes']} B): [{info['state']}] {info['text']}"
                  f"   <{info['source']}>", flush=True)
        if a.preview_dir:
            os.makedirs(a.preview_dir, exist_ok=True)
            if kind == "status":
                img = render_status(ctl.detection)[0][0]
            else:
                img = render_buddy_preview(ctl.last_buddy or BD.Activity(info["state"], info["text"]))
            img.resize((W * 10, H * 10), Image.Resampling.NEAREST).save(
                os.path.join(a.preview_dir, f"{kind}_{ctl.sends:03d}.png"))

    async def run():
        sign = None
        if not a.dry_run:
            from .ble_sign import SignClient
            sign = SignClient(a.address or None, rotate_180=a.rotate)
            await sign.connect()
        global ctl
        ctl = AIStatus(sign, buddy=a.buddy, dry_run=a.dry_run, emit=emit)
        stop = {"flag": False}
        ctl.on_takeover = lambda: stop.update(flag=True)
        last_seen = None
        t_end = time.monotonic() + a.seconds if a.seconds else None
        try:
            while not stop["flag"] and (t_end is None or time.monotonic() < t_end):
                await ctl.tick()
                d = ctl.detection
                act = ctl.activity
                seen = (_det_summary(d) or {}).get("app"), (d.key() if d else None), (act.state, act.text) if act else None
                if seen != last_seen:
                    fg = ctl.watcher.last_fg
                    s = _det_summary(d) or {}
                    print(f"{time.strftime('%H:%M:%S')} detect fg={fg.exe if fg else None!r} title={fg.title[:40] if fg else ''!r} "
                          f"-> app={s.get('app')} repo={s.get('repo')} branch={s.get('branch')} "
                          f"worktree={s.get('worktree')} | activity="
                          f"{('[%s] %s' % (act.state, act.text)) if act else None}", flush=True)
                    last_seen = seen
                await asyncio.sleep(a.interval)
        finally:
            if sign is not None:
                await sign.disconnect()
        print(f"done: {ctl.sends} sends", flush=True)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        if not a.dry_run:
            print("hint: the web server may hold the BLE connection; use its Auto status toggle, "
                  "or run with --dry-run", file=sys.stderr)
        return 2
    return 0


ctl: AIStatus | None = None
