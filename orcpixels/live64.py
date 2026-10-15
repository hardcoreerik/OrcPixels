"""64x64 layouts for every Live Data screen (2026-10-08, the 64x64 matrix LED_BLE_d0736074).

Same screens, keys, feeds, palette and loop rules as the 64x16 set (livedata_screens*.py,
livedata_ft8.py); each one gets its own 64x64 layout instead of a letterboxed strip:

  rows 0-8   header band: screen title in the family colour (+ a right-hand readout)
  rows 10-63 body: 2x unifont values, 2x icons, maps, charts, multi-row lists

Animation index k and the loop length come from the 64x16 screen (loop64() overrides it
where the 64x64 layout animates differently, e.g. the bottom marquees), so the page
boundary is still offset by half a page and frame loop-1 -> 0 is an ordinary step.
`render(key, feed, k, now)` mirrors livedata_screens.render(); SC.render(..., size=(64, 64))
calls it.
"""
from __future__ import annotations

import math
import time
from functools import lru_cache

from PIL import Image, ImageDraw

from . import place as PLACE
from . import livedata_screens as SC
from .livedata_screens import (AMB, CREAM, DAMB, DIM, DOG, DPB, GREY, LPB, OG, OG2, PB, RED, ROOT,
                               SKY, STALE, WHITE, _uni, big_w, dimc, fit, page_of, wrap)
from .sources import livefeeds as LF
from .tinyfont import draw_text, text_width

W = H = 64
SIZE = (64, 64)
BODY = 10                     # first body row under the header band


# ------------------------------------------------------------------ drawing helpers
def put(img, x, y, c):
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((int(x), int(y)), c)


def T(img, x, y, s, c) -> int:
    return draw_text(img, int(x), int(y), s, c)


def RT(img, xr, y, s, c) -> int:
    return draw_text(img, int(xr - text_width(s) + 1), int(y), s, c)


def CT(img, y, s, c, x0=0, x1=63):
    return T(img, x0 + (x1 - x0 + 1 - text_width(s)) // 2, y, s, c)


def U(img, x, y, s, c) -> int:
    """Unifont 8x16 (caps/digits ~ rows y+2..y+13)."""
    return SC.big(img, int(x), int(y), s, c)


@lru_cache(maxsize=512)
def _u2mask(s: str):
    w = max(1, big_w(s))
    m = Image.new("L", (w, 16))
    ImageDraw.Draw(m).text((0, 0), s, fill=255, font=_uni())
    m = m.point(lambda v: 255 if v > 128 else 0)
    return m.resize((w * 2, 32), Image.Resampling.NEAREST)


def U2(img, x, y, s, c) -> int:
    """Unifont at 2x (16x32 cells; digits ~ rows y+4..y+27). Returns end x."""
    if not s:
        return x
    m = _u2mask(s)
    img.paste(Image.new("RGB", m.size, c), (int(x), int(y)), m)
    return int(x) + m.width


def u2w(s: str) -> int:
    return 2 * big_w(s)


def UC(img, y, s, c, x0=0, x1=63):
    return U(img, x0 + (x1 - x0 + 1 - big_w(s)) // 2, y, s, c)


def U2C(img, y, s, c, x0=0, x1=63):
    return U2(img, x0 + (x1 - x0 + 1 - u2w(s)) // 2, y, s, c)


def header(img, title: str, col, right: str | None = None, rcol=GREY):
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 63, 8), fill=dimc(col, 0.16))
    d.line((0, 8, 63, 8), fill=dimc(col, 0.45))
    if right and text_width(title) + 4 + text_width(right) > 60:
        right = None                         # the title wins; the readout is optional
    T(img, 2, 2, fit(title, 60), col)
    if right:
        RT(img, 61, 2, right, rcol)


def icon2(img, x, y, fn, w=13, h=13, oy=2):
    """Draw a 64x16-set icon (fn(tmp) draws at (0, oy) like the original) and paste it 2x."""
    tmp = Image.new("RGB", (64, 16))
    fn(tmp)
    ic = tmp.crop((0, oy - 1 if oy else 0, w, oy - 1 + h if oy else h)).resize((w * 2, h * 2), Image.Resampling.NEAREST)
    mask = ic.convert("L").point(lambda v: 255 if v else 0)
    img.paste(ic, (int(x), int(y)), mask)


def dots(img, n, cur, y=63, on=LPB, off=DIM, x1=63):
    if n <= 1:
        return
    n = min(n, 16)
    x0 = x1 - 2 * (n - 1)
    for i in range(n):
        put(img, x0 + 2 * i, y, on if i == cur else off)


def hbar(img, x0, y0, x1, y1, col):
    ImageDraw.Draw(img).rectangle((x0, y0, x1, y1), fill=col)


def spark(img, x0, y0, w, h, series, prev=None, col=None, k=0, fill=None):
    pts = [p for p in series if p is not None]
    if len(pts) < 2:
        return
    vals = [pts[int(i * (len(pts) - 1) / (w - 1))] for i in range(w)]
    ext = vals + ([prev] if prev else [])
    lo, hi = min(ext), max(ext)
    span = (hi - lo) or 1.0
    yv = lambda v: y0 + h - 1 - int(round((v - lo) / span * (h - 1)))
    from .livedata_screens2 import chg_col
    col = col or chg_col(vals[-1] - (prev if prev else vals[0]))
    d = ImageDraw.Draw(img)
    if fill:
        for i, v in enumerate(vals):
            d.line((x0 + i, yv(v) + 1, x0 + i, y0 + h - 1), fill=fill)
    if prev:
        for x in range(0, w, 2):
            put(img, x0 + x, yv(prev), DIM)
    last = None
    for i, v in enumerate(vals):
        p = (x0 + i, yv(v))
        if last:
            d.line((last, p), fill=dimc(col, 0.9))
        last = p
    put(img, *last, CREAM if k % 2 == 0 else col)


def wrapn(s: str, px: int, lines: int) -> list[str]:
    return wrap(s, px, lines)


# ------------------------------------------------------------------ registry
class S64:
    """64x64 layout of one Live Data screen."""
    key = ""
    title = ""
    col = LPB

    def loop(self, v) -> int:
        return SC.SCREENS[self.key].loop(v)

    def draw(self, img, v, k, now):
        raise NotImplementedError


DRAW: dict[str, S64] = {}


def reg(cls):
    DRAW[cls.key] = cls()
    return cls


# ================================================================== weather, sky + radio
@reg
class WxNow64(S64):
    key, title, col = "wx_now", PLACE.Lbl(lambda: f"{PLACE.home_tag()} NOW"), OG

    def draw(self, img, v, k, now):
        night = v.get("night")
        cond = SC.cond_of(v.get("desc", ""), night)
        header(img, self.title, OG, LF.hm12(now), GREY)
        icon2(img, 0, 12, lambda t: SC.wx_icon(t, 0, 2, cond, night, k))
        t = v.get("temp_f")
        tc = LPB if t is not None and t < 45 else (AMB if t is not None and t >= 85 else CREAM)
        s = "--" if t is None else f"{t}"
        x = U2(img, 28, 8, s, tc)
        T(img, x, 13, "\u00b0", tc)
        T(img, x, 20, "F", GREY)
        UC(img, 35, SC.SHORT[cond], OG2)
        wind = "CALM" if not v.get("wind_mph") else f"WIND {v.get('wind_dir', '')} {v['wind_mph']}MPH"
        CT(img, 52, fit(wind, 64), LPB)
        parts = []
        if v.get("rh") is not None:
            parts.append(f"RH {v['rh']}%")
        if v.get("pressure_mb"):
            parts.append(f"{v['pressure_mb']}MB")
        CT(img, 58, fit("  ".join(parts) or PLACE.home_tag(), 64), GREY)


def _day_name(q) -> str:
    name = q["name"].upper().replace("THIS ", "").replace("NIGHT", "NT").replace("AFTERNOON", "PM")
    for full, ab in (("MONDAY", "MON"), ("TUESDAY", "TUE"), ("WEDNESDAY", "WED"), ("THURSDAY", "THU"),
                     ("FRIDAY", "FRI"), ("SATURDAY", "SAT"), ("SUNDAY", "SUN"), ("TONT", "TONIGHT")):
        name = name.replace(full, ab)
    return name


@reg
class Forecast64(S64):
    key, title, col = "forecast", "FORECAST", AMB

    def draw(self, img, v, k, now):
        pers = (v.get("periods") or [])[:4]
        header(img, self.title, AMB, f"NWS {PLACE.home_tag()}", GREY)
        sel = page_of(k, max(1, len(pers)), SC.SCREENS[self.key].per) if pers else 0
        for i, q in enumerate(pers):
            y = BODY + 1 + i * 13
            SC.wx_icon(img, 0, y, SC.cond_of(q["short"]), not q["day"], k if i == sel else 0)
            on = i == sel
            tmp = f"{q['temp']}\u00b0" if q.get("temp") is not None else ""
            T(img, 14, y, fit(_day_name(q), 48 - text_width(tmp) - 2), AMB if on else DAMB)
            RT(img, 63, y, tmp, (CREAM if q["day"] else LPB) if on else GREY)
            T(img, 14, y + 6, fit(q["short"], 50), OG2 if on else dimc(OG, 0.7))


@reg
class Alerts64(S64):
    key, title, col = "alerts", PLACE.Lbl(lambda: f"{PLACE.alerts_tag() or 'WX'} ALERTS"), AMB

    def draw(self, img, v, k, now):
        al = v.get("alerts") or []
        if not al:
            header(img, self.title, OG, "NWS", GREY)
            icon2(img, 20, 13, lambda t: SC.check_icon(t, 0, 2, k))
            UC(img, 40, "NO ALERTS", OG2)
            CT(img, 57, "ALL CLEAR", GREY)
            return
        per = SC.SCREENS[self.key].per
        n = SC.SCREENS[self.key].pages(v)
        i = page_of(k, n, per) % len(al)
        a = al[i]
        col = {"Extreme": RED, "Severe": RED, "Moderate": AMB}.get(a["severity"], (240, 220, 40))
        header(img, self.title, col, f"{i + 1}/{len(al)}", LPB)
        icon2(img, 0, 11, lambda t: SC.warn_icon(t, 0, 2, k, col))
        lines = wrapn(a["event"], 36, 4)
        for j, line in enumerate(lines):
            T(img, 28, 12 + 6 * j, line, col if j == 0 else dimc(col, 0.8))
        y = 38
        if a.get("ends"):
            T(img, 0, y, "UNTIL " + LF.hm12(a["ends"]), CREAM)
            y += 7
        for line in wrapn(a.get("area", ""), 64, 3 if y < 40 else 2):
            T(img, 0, y, line, LPB)
            y += 6
        sev = (a.get("severity") or "").upper()
        if sev:
            T(img, 0, 58, fit(sev, 40), dimc(col, 0.9))
        dots(img, min(len(al), n), i, 63, col)


@reg
class Aqi64(S64):
    key, title, col = "aqi", PLACE.Lbl(lambda: f"AIR QUALITY {PLACE.home_tag()}"), LPB

    def draw(self, img, v, k, now):
        a = int(v["aqi"])
        col, name = SC.gauge_col(a)
        header(img, self.title, LPB)
        U2C(img, 8, str(a), col)
        UC(img, 34, name, col)
        x = int(round(63 * min(a, 300) / 300))
        segs = ((50, SC.OG), (100, (240, 220, 40)), (150, AMB), (200, RED), (300, (190, 60, 220)))
        lo = 0
        for lim, c in segs:
            hbar(img, int(lo * 63 / 300), 50, int(lim * 63 / 300), 52, dimc(c, 0.45))
            lo = lim
        hbar(img, 0, 50, max(0, x), 52, col)
        put(img, x, 49, CREAM if k % 2 == 0 else col)
        put(img, x, 53, CREAM if k % 2 == 0 else col)
        pm25 = f"PM2.5 {v['pm25']:.0f}" if v.get("pm25") is not None else "PM2.5 --"
        pm10 = f"PM10 {v['pm10']:.0f}" if v.get("pm10") is not None else "PM10 --"
        T(img, 0, 58, pm25, GREY)
        RT(img, 63, 58, pm10, GREY)


@reg
class SpaceWx64(S64):
    key, title, col = "spacewx", "SPACE WX", OG

    def draw(self, img, v, k, now):
        kps = v["kp"][-8:]
        cur = kps[-1]
        header(img, self.title, SC.kp_col(cur), "NOAA", GREY)
        d = ImageDraw.Draw(img)
        base, top = 38, 12
        for i, kp in enumerate(kps):
            h = max(1, min(base - top, int(round(kp / 9 * (base - top))) + 1))
            x = i * 8
            d.rectangle((x, base - h + 1, x + 5, base), fill=dimc(SC.kp_col(kp), 0.75))
            if i == len(kps) - 1:
                d.rectangle((x, base - h + 1, x + 5, base - h + 1), fill=CREAM if k % 2 == 0 else SC.kp_col(kp))
        g1 = base - int(round(5 / 9 * (base - top)))
        for x in range(0, 64, 3):
            if img.getpixel((x, g1)) == (0, 0, 0):
                put(img, x, g1, DIM)
        g = int(cur) - 4 if cur >= 5 else 0
        state = f"G{g} STORM" if g else ("ACTIVE" if cur >= 4 else "QUIET")
        x = U(img, 0, 38, f"KP {cur:.1f}".replace(".0", ""), SC.kp_col(cur))
        T(img, 0, 52, state, SC.kp_col(cur))
        RT(img, 63, 52, "3H KP", DIM)
        sp = v.get("speed")
        T(img, 0, 58, f"SW {int(sp)}KM/S" if sp is not None else "SW --", AMB if (sp or 0) > 500 else LPB)
        bz = v.get("bz")
        RT(img, 63, 58, f"BZ {bz:+.0f}" if bz is not None else "BZ --",
           RED if bz is not None and bz <= -10 else (AMB if bz is not None and bz < -5 else LPB))
        _ = x


@reg
class XRay64(S64):
    key, title, col = "xray", "SOLAR X-RAY", AMB

    def draw(self, img, v, k, now):
        cls = v["current"]
        col = SC.xr_col(cls)
        header(img, self.title, AMB)
        d = ImageDraw.Draw(img)
        cx, cy = 58, 4                                   # little sun in the header
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=AMB)
        for i, (fx, fy) in enumerate(((cx + 4, cy - 2), (cx - 4, cy + 2), (cx + 4, cy + 3), (cx - 4, cy - 3))):
            if (k + i) % 4 < 2:
                put(img, fx, fy, col if cls[0] in "CMX" else CREAM)
        U2C(img, 8, cls[:4], col)
        segs = "ABCMX"
        ci = segs.find((cls or "A")[0].upper())
        for i, s in enumerate(segs):
            x0 = i * 13
            c = SC.xr_col(s)
            hbar(img, x0, 40, x0 + 11, 42, c if i <= ci else dimc(c, 0.3))
            T(img, x0 + 4, 44, s, c if i == ci and k % 2 == 0 else dimc(c, 0.7))
        if v.get("max_class"):
            x = T(img, 0, 52, "MAX", GREY)
            x = T(img, x + 2, 52, v["max_class"], SC.xr_col(v["max_class"]))
            if v.get("max_time"):
                RT(img, 63, 52, LF.ago(v["max_time"], now), LPB)
        if v.get("sfi") is not None:
            T(img, 0, 58, f"SFI {v['sfi']:.0f}", LPB)
        RT(img, 63, 58, "24H", DIM)


@reg
class HfBands64(S64):
    key, title, col = "hf_bands", "HF BANDS", AMB

    def draw(self, img, v, k, now):
        header(img, self.title, AMB, "HAMQSL", GREY)
        day = 7 <= LF.pt(now).hour < 19
        T(img, 26, 11, "DAY", (CREAM if k % 2 == 0 else AMB) if day else GREY)
        T(img, 45, 11, "NIGHT", (CREAM if k % 2 == 0 else AMB) if not day else GREY)
        for i, b in enumerate(SC.BANDS):
            y = 18 + i * 8
            T(img, 0, y + 1, b.replace("m", "").upper(), LPB)
            for j, half in enumerate(("day", "night")):
                cond = v["bands"].get(b, {}).get(half, "")
                c = SC.CONDC.get(cond, DIM)
                on = (half == "day") == day
                x0 = 26 + j * 19
                hbar(img, x0, y, x0 + 16, y + 6, c if on else dimc(c, 0.5))
                lab = {"Good": "GD", "Fair": "FR", "Poor": "PR"}.get(cond, "--")
                T(img, x0 + 5, y + 1, lab, (0, 0, 0))
        T(img, 0, 52, f"SFI {v['sfi']}", AMB)
        CT(img, 52, f"SN {v['sn']}", AMB, 22, 44)
        RT(img, 63, 52, f"K {v['k']}", LPB)
        T(img, 0, 58, fit(v.get("geomag", ""), 40), OG2)
        RT(img, 63, 58, fit(v.get("noise", ""), 22), GREY)


@lru_cache(None)
def world64():
    return Image.open(ROOT / "assets" / "world_land_64x32.png").convert("L")


def wxy(lat, lon):
    return int((lon + 180) / 360 * 64) % 64, max(0, min(31, int((90 - lat) / 180 * 32)))


def dur(s):
    return SC.dur(s)


@reg
class Iss64(S64):
    key, title, col = "iss", "ISS", AMB

    def draw(self, img, v, k, now):
        header(img, self.title, AMB, f"{v['alt_km']:.0f}KM" if v.get("alt_km") else None, DIM)
        img.paste(Image.new("RGB", (64, 32), (0, 6, 22)), (0, BODY))
        img.paste(Image.new("RGB", (64, 32), (16, 72, 34)), (0, BODY), world64())
        for la, lo in v.get("track") or []:
            x, y = wxy(la, lo)
            put(img, x, BODY + y, DAMB)
        if LF.HOME:
            hx, hy = wxy(*LF.HOME)
            put(img, hx, BODY + hy, LPB)
        x, y = wxy(v["lat"], v["lon"])
        c = CREAM if k % 2 == 0 else AMB
        put(img, x, BODY + y, c)
        if k % 2 == 0:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                put(img, x + dx, BODY + y + dy, dimc(AMB, 0.6))
        pos = f"{abs(v['lat']):.0f}{'N' if v['lat'] >= 0 else 'S'} {abs(v['lon']):.0f}{'E' if v['lon'] >= 0 else 'W'}"
        ps = v.get("pass")
        if not ps:
            T(img, 0, 45, pos, LPB)
            T(img, 0, 53, "NO PASS SOON", GREY)
            return
        T(img, 0, 45, "PASS IN", LPB)
        RT(img, 63, 45, f"MAX {ps['max_el']}\u00b0", OG2 if ps["max_el"] >= 40 else GREY)
        if ps["in_progress"] or ps["aos"] <= now <= ps["los"]:
            hbar(img, 0, 44, 63, 51, (0, 0, 0))
            T(img, 0, 45, "NOW", OG2)
            U(img, 0, 49, "OVERHEAD", OG2 if k % 2 == 0 else OG)
            return
        ds = dur(ps["aos"] - now)
        if big_w(ds) <= 44:
            U(img, 0, 49, ds, CREAM)
        else:
            T(img, 0, 54, ds, CREAM)
        RT(img, 63, 52, "AT", GREY)
        RT(img, 63, 58, LF.hm12(ps["aos"]).replace("M", ""), CREAM)


@reg
class Quake64(S64):
    key, title, col = "quake", PLACE.Lbl(lambda: f"{PLACE.quake_tag()} QUAKES".strip()), AMB

    def draw(self, img, v, k, now):
        qs = v.get("quakes") or []
        header(img, self.title, AMB, "USGS", GREY)
        trace = [0, 0, -1, 2, -3, 1, -1, 0, 0, 1, -4, 3, -2, 1, 0, 0]
        amp = 1.0 if not qs else min(2.5, 0.6 + qs[0]["mag"] / 2)
        for x in range(64):
            a = int(round(trace[(x + 4 * k) % 16] * amp))
            put(img, x, 16 + a, OG if a == 0 else AMB)
        if not qs:
            UC(img, 26, "NONE", OG2)
            CT(img, 46, "NO M1.5+ TODAY", LPB)
            return
        q = qs[0]
        col = SC.mag_col(q["mag"])
        x = U2(img, 0, 20, f"{q['mag']:.1f}", col)
        T(img, x + 1, 26, "M", col)
        RT(img, 63, 26, LF.ago(q["time"], now), LPB)
        RT(img, 63, 33, "AGO", GREY)
        for i, line in enumerate(wrapn(SC.short_place(q["place"]), 64, 2)):
            T(img, 0, 46 + 6 * i, line, LPB if i == 0 else OG2)
        if len(qs) > 1:
            q2 = qs[1]
            x = T(img, 0, 58, f"M{q2['mag']:.1f}", SC.mag_col(q2["mag"]))
            T(img, x + 2, 58, fit(LF.ago(q2["time"], now) + " " + SC.short_place(q2["place"]), 62 - x), GREY)


@reg
class Aircraft64(S64):
    key, title, col = "aircraft", "AIRCRAFT", OG

    def draw(self, img, v, k, now):
        header(img, self.title, OG, PLACE.home_tag(), GREY)
        d = ImageDraw.Draw(img)
        cx, cy, r = 13, 26, 12
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=DOG)
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline=dimc(DOG, 0.7))
        for i in range(3):
            a = (k % 4) * math.pi / 2 - math.pi / 2 - i * 0.35
            d.line((cx, cy, cx + r * math.cos(a), cy + r * math.sin(a)), fill=dimc(OG, 1 - 0.3 * i))
        put(img, cx, cy, CREAM)
        n = v.get("count", 0)
        x = U2(img, 30, 8, str(n), CREAM)
        T(img, 30, 36, "IN RANGE", GREY)
        cl = (v.get("closest") or [])[:3]
        if not cl:
            T(img, 0, 46, "NONE CLOSE", GREY)
            return
        per = SC.SCREENS[self.key].per
        sel = page_of(k, SC.SCREENS[self.key].pages(v), per) % len(cl)
        for i, c in enumerate(cl):
            y = 45 + 6 * i
            alt = c.get("alt_ft")
            alt_s = "--" if alt is None else (f"FL{alt // 100:03d}" if alt >= 18000 else f"{alt}")
            on = i == sel
            T(img, 0, y, fit(c["call"], 26), AMB if on else DAMB)
            T(img, 27, y, alt_s, LPB if on else dimc(LPB, 0.5))
            RT(img, 63, y, f"{c['km']:.0f}K", CREAM if on else GREY)
        _ = x


@reg
class Tides64(S64):
    key, title, col = "tides", "TIDES", PB

    def draw(self, img, v, k, now):
        ev = v["events"]
        header(img, self.title, LPB, PLACE.tide_tag(), GREY)
        hs = [LF.tide_height(ev, now + (x - 8) * 1350) for x in range(64)]       # -3 h .. +21 h
        known = [h for h in hs if h is not None]
        lo, hi = (min(known), max(known)) if known else (0, 1)
        span = max(0.5, hi - lo)
        d = ImageDraw.Draw(img)
        top, bot = 13, 40
        for x, h in enumerate(hs):
            if h is None:
                continue
            y = bot - int(round((h - lo) / span * (bot - top)))
            d.line((x, y + 1, x, bot), fill=DPB)
            put(img, x, y, LPB)
        nowh = hs[8]
        if nowh is not None:
            y = bot - int(round((nowh - lo) / span * (bot - top)))
            for yy in range(top, bot + 1, 2):
                put(img, 8, yy, DIM)
            put(img, 8, y, CREAM if k % 2 == 0 else AMB)
        nxt = [e for e in ev if e["t"] > now][:3]
        sel = page_of(k, 2, SC.SCREENS[self.key].per)
        for i, e in enumerate(nxt):
            y = 45 + 6 * i
            on = i == min(sel, len(nxt) - 1)
            hc = AMB if e["type"] == "H" else LPB
            T(img, 0, y, "HIGH" if e["type"] == "H" else "LOW", hc if on else dimc(hc, 0.55))
            T(img, 20, y, LF.hm12(e["t"]), CREAM if on else GREY)
            RT(img, 63, y, f"{e['v']:.1f}FT", OG2 if on else dimc(OG2, 0.5))


@reg
class River64(S64):
    key, title, col = "river", PLACE.Lbl(PLACE.river_tag), OG2

    def draw(self, img, v, k, now):
        header(img, self.title, OG2, "USGS", GREY)
        x = U2(img, 0, 8, f"{v['ft']:.1f}", CREAM)
        T(img, x + 2, 26, "FT", GREY)
        tr = v.get("trend")
        if tr is not None:
            arrow = "+" if tr > 0.05 else ("-" if tr < -0.05 else "=")
            c = OG if arrow == "+" else (AMB if arrow == "-" else GREY)
            RT(img, 63, 13, f"{arrow}{abs(tr):.1f}", c)
            RT(img, 63, 19, "3H", GREY)
        ser = v["series"]
        n = 64
        pts = [ser[int(i * (len(ser) - 1) / (n - 1))] for i in range(n)] if len(ser) > 1 else ser * n
        lo, hi = min(pts), max(pts)
        span = max(0.3, hi - lo)
        d = ImageDraw.Draw(img)
        top, bot = 40, 62
        for i, h in enumerate(pts):
            y = bot - int(round((h - lo) / span * (bot - top)))
            d.line((i, y + 1, i, bot), fill=DPB)
            put(img, i, y, LPB)
        put(img, n - 1, bot - int(round((pts[-1] - lo) / span * (bot - top))), CREAM if k % 2 == 0 else AMB)
        for i in range(n):
            if (i + k * 4) % 16 == 0:
                put(img, i, bot + 1, (60, 120, 200))
                put(img, i + 1, bot, (60, 120, 200))
        T(img, 0, 34, f"{hi:.1f} MAX", DIM)


@reg
class SunMoon64(S64):
    key, title, col = "sunmoon", "SUN + MOON", AMB

    def draw(self, img, v, k, now):
        header(img, self.title, AMB, PLACE.home_tag(), GREY)
        d = ImageDraw.Draw(img)
        d.pieslice((0, 14, 23, 37), 180, 360, fill=AMB)
        d.line((0, 26, 23, 26), fill=DAMB)
        for i, (dx, dy) in enumerate(((11, 10), (2, 16), (21, 16), (11, 11))):
            if (k + i) % 4 < 2:
                put(img, dx, dy, CREAM)
        rise = v["next_rise"] if v.get("next_rise") else v.get("rise")
        T(img, 27, 12, "RISE", AMB)
        RT(img, 63, 12, LF.hm12(rise) if rise else "--", CREAM)
        T(img, 27, 19, "SET", (255, 120, 60))
        RT(img, 63, 19, LF.hm12(v["set"]) if v.get("set") else "--", CREAM)
        if v.get("day_s"):
            ds = int(v["day_s"])
            T(img, 27, 26, "DAY", GREY)
            RT(img, 63, 26, f"{ds // 3600}H{(ds % 3600) // 60:02d}", GREY)
        m = v["moon"]
        icon2(img, 0, 36, lambda t: SC.moon_icon(t, 0, 2, m["illum"], m["waxing"]), 12, 12)
        if k % 2 == 0:
            put(img, 25, 37, LPB)
        T(img, 27, 39, "MOON", LPB)
        for i, line in enumerate(wrapn(m["name"], 37, 2)):
            T(img, 27, 46 + 6 * i, line, CREAM)
        RT(img, 63, 58, f"{m['illum'] * 100:.0f}% LIT", GREY)


@reg
class Clock64(S64):
    key, title, col = "clock", "UTC", AMB

    def draw(self, img, v, k, now):
        u = time.gmtime(now)
        p = LF.pt(now)
        header(img, self.title, AMB, time.strftime("%a %b %d", u).upper(), GREY)
        U2(img, -1, 10, f"{u.tm_hour:02d}", CREAM)
        U2(img, 33, 10, f"{u.tm_min:02d}", CREAM)
        if k % 2 == 0:
            hbar(img, 31, 18, 32, 19, AMB)
            hbar(img, 31, 28, 32, 29, AMB)
        hbar(img, 0, 42, 63, 42, (20, 16, 6))
        hbar(img, 0, 42, int(u.tm_sec / 59 * 63), 42, DAMB)
        x = T(img, 0, 50, PLACE.tz_label(now), LPB)
        U(img, x + 3, 45, f"{p.hour:02d}:{p.minute:02d}", LPB)


@reg
class GitHub64(S64):
    key, title, col = "github", PLACE.Lbl(lambda: (str(PLACE.get("livedata_github_user") or "GITHUB")).upper()), OG2

    def draw(self, img, v, k, now):
        header(img, "GITHUB", OG2, (str(PLACE.get("livedata_github_user") or "")).upper()[:12], DIM)

        def ic(t):
            dd = ImageDraw.Draw(t)
            dd.line((3, 2, 3, 13), fill=GREY)
            dd.line((3, 9, 8, 5), fill=GREY)
            dd.ellipse((1, 11, 5, 15), outline=OG2)
            dd.ellipse((1, 0, 5, 4), outline=OG2)
            dd.ellipse((7, 3, 11, 7), fill=AMB if k % 2 == 0 else DAMB)
        icon2(img, 0, 11, ic, 13, 16, 0)
        x = U(img, 30, 10, str(v["stars"]), AMB)
        T(img, 30, 26, "STARS", GREY)
        T(img, 30, 33, f"{v['repos']} REPOS", LPB)
        sel = page_of(k, 3, SC.SCREENS[self.key].per)
        T(img, 0, 45, fit(v["last"], 64), OG2 if sel != 2 else dimc(OG2, 0.6))
        T(img, 0, 51, fit("PUSH " + LF.ago(v.get("last_at"), now) + " AGO", 64), GREY)
        name, stars = (v.get("top") or [("?", 0)])[0]
        T(img, 0, 58, fit("TOP " + name, 44), CREAM if sel == 2 else dimc(CREAM, 0.6))
        RT(img, 63, 58, f"{stars}*", AMB)
        _ = x


# ================================================================== markets
from . import livedata_screens2 as S2  # noqa: E402
from .sources import marketnews as MN  # noqa: E402

MSTEP = S2.STEP


def marquee64(img, segs: tuple, k: int, y: int):
    """Bottom marquee (unifont, same strip + step as the 64x16 ticker: seamless)."""
    s = S2.strip(segs, MSTEP)
    o = (k * MSTEP) % s.width
    x = -o
    band = Image.new("RGB", (64, 16))
    while x < 64:
        band.paste(s, (x, 0))
        x += s.width
    img.paste(band, (0, y))


@reg
class Stocks64(S64):
    key, title, col = "stocks", "STOCKS", AMB

    def loop(self, v):
        return SC.SCREENS[self.key].loop(v)

    def draw(self, img, v, k, now):
        st_t, st_c = S2.state_seg(now)
        header(img, "STOCKS", AMB, st_t.strip(), st_c)
        qs = (v.get("quotes") or [])[:5]
        for i, q in enumerate(qs):
            y = 11 + 7 * i
            pct = S2.fmt_pct(q.get("pct"))
            pc = S2.chg_col(q.get("pct"))
            T(img, 0, y, fit(q["name"], 20), AMB)
            pw = RT(img, 63, y, pct, pc)
            xr = 63 - text_width(pct) - 3
            RT(img, xr, y, fit(S2.fmt_price(q["price"]), xr - 22), CREAM)
            _ = pw
        hbar(img, 0, 46, 63, 46, (30, 24, 8))
        marquee64(img, SC.SCREENS[self.key].segs(v, now), k, 48)


class Quote64(S64):
    col = AMB

    def draw(self, img, v, k, now):
        qs = v["quotes"]
        scr = SC.SCREENS[self.key]
        if not qs:
            header(img, scr.short, AMB)
            T(img, 0, 20, "NO QUOTES", AMB)
            return
        p = page_of(k, len(qs), scr.per)
        q = qs[p]
        pc = S2.chg_col(q.get("pct"))
        header(img, fit(q["name"], 40), AMB, f"{p + 1}/{len(qs)}", DIM)
        price = S2.fmt_price(q["price"], q.get("yield"))
        if big_w(price) <= 64:
            U(img, 0, 9, price, CREAM)
        else:
            T(img, 0, 13, price, CREAM)
        T(img, 0, 26, fit(S2.fmt_chg(q) if not q.get("yield") else (f"PRV {q['prev']:.2f}" if q.get("prev") else ""), 30), dimc(pc, 0.85))
        RT(img, 63, 26, S2.fmt_pct(q.get("pct")) if not q.get("yield") else S2.fmt_chg(q), pc)
        spark(img, 0, 33, 64, 22, q.get("series") or [], q.get("prev"), pc, k, fill=dimc(pc, 0.18))
        names = [x["name"] for x in qs]
        x = 0
        for i, nm in enumerate(names):
            s = nm.upper() if text_width(nm.upper()) <= 20 else nm.upper().split()[0][:5]
            if x + text_width(s) > 64:
                break
            x = T(img, x, 58, s, AMB if i == p else DIM) + 3


for _k, _t in (("indexes", "INDEXES"), ("commod", "GOLD OIL"), ("rates", "RATES")):
    reg(type(f"Quote64_{_k}", (Quote64,), {"key": _k, "title": _t}))


@reg
class Spark64(S64):
    key, title, col = "spark", "SPARK", AMB

    def draw(self, img, v, k, now):
        q = v["quotes"][0]
        pc = S2.chg_col(q.get("pct"))
        header(img, fit(q["name"], 36), AMB, S2.fmt_pct(q.get("pct")), pc)
        price = S2.fmt_price(q["price"], q.get("yield"))
        U(img, 0, 9, price, CREAM) if big_w(price) <= 64 else T(img, 0, 13, price, CREAM)
        RT(img, 63, 26, fit(S2.fmt_chg(q), 30), dimc(pc, 0.85))
        T(img, 0, 26, "1D", DIM)
        spark(img, 0, 33, 64, 30, q.get("series") or [], q.get("prev"), pc, k, fill=dimc(pc, 0.2))


def _cfmt(p):
    return f"{p:.0f}" if p >= 1000 else (f"{p:.1f}" if p >= 100 else (f"{p:.2f}" if p >= 1 else f"{p:.3f}"))


@reg
class Crypto64(S64):
    key, title, col = "crypto", "CRYPTO", AMB

    def draw(self, img, v, k, now):
        cs = v["coins"]
        p = page_of(k, len(cs), SC.SCREENS[self.key].per)
        c = cs[p]
        header(img, "CRYPTO 24H", AMB, "USD", GREY)
        pc = S2.chg_col(c.get("pct"))
        T(img, 0, 12, fit(c["sym"], 30), AMB if k % 2 == 0 else DAMB)
        RT(img, 63, 12, S2.fmt_pct(c.get("pct")), pc)
        s = _cfmt(c["price"])
        U2C(img, 14, s, CREAM) if u2w(s) <= 64 else UC(img, 20, s, CREAM)
        hbar(img, 0, 46, 63, 46, dimc(pc, 0.4))
        for i, cc in enumerate(cs[:2] if len(cs) <= 2 else [cs[(p + 1) % len(cs)], cs[(p + 2) % len(cs)]]):
            y = 49 + 7 * i
            T(img, 0, y, fit(cc["sym"], 18), dimc(AMB, 0.7))
            T(img, 20, y, _cfmt(cc["price"]), GREY)
            RT(img, 63, y, S2.fmt_pct(cc.get("pct")), dimc(S2.chg_col(cc.get("pct")), 0.8))


@reg
class Forex64(S64):
    key, title, col = "forex", "FOREX", AMB

    def draw(self, img, v, k, now):
        prs = v["pairs"][:7]
        date = (v.get("date") or "")[5:].replace("-", "/")
        header(img, "FOREX", AMB, f"ECB {date}" if date else "ECB", GREY)
        n = SC.SCREENS[self.key].pages(v)
        p = page_of(k, n, SC.SCREENS[self.key].per)
        for i, pr in enumerate(prs):
            y = 11 + 7 * i
            on = i // 2 == p
            r = pr["rate"]
            rs = f"{r:.2f}" if r >= 100 else (f"{r:.3f}" if r >= 10 else f"{r:.4f}")
            if on:
                hbar(img, 0, y - 1, 63, y + 5, (10, 22, 40))
            T(img, 1, y, pr["pair"], LPB if on else dimc(LPB, 0.55))
            RT(img, 62, y, rs, CREAM if on else GREY)


@reg
class MarketClock64(S64):
    key, title, col = "market_clock", "NYSE", AMB

    def draw(self, img, v, k, now):
        st = MN.market_state(now)
        open_ = st["state"] == "open"
        name, scol = {"open": ("OPEN", OG), "pre": ("PRE-MARKET", AMB), "after": ("AFTER HOURS", AMB)}.get(
            st["state"], ("CLOSED", RED))
        header(img, "NYSE", AMB, LF.hm12(now), GREY)
        UC(img, 9, name, scol)
        target = st["close"] if open_ else st["open"]
        cd = S2.countdown((target or now) - now)
        col = OG2 if open_ else CREAM
        T(img, 0, 28, "CLOSES IN" if open_ else "OPENS IN", GREY)
        shown = cd if (":" not in cd or k % 2 == 0) else cd.replace(":", " ")
        if u2w(shown) <= 64:
            U2C(img, 30, shown, col)
        else:
            UC(img, 38, shown, col)
        if target:
            same = LF.pt(target).date() == LF.pt(now).date()
            day = "TODAY" if same else LF.pt(target).strftime("%a").upper()
            T(img, 0, 58, day, LPB)
            RT(img, 63, 58, LF.hm12(target), LPB)
        if open_:
            frac = (now - st["open"]) / max(1.0, st["close"] - st["open"])
            hbar(img, 0, 25, 63, 25, (10, 30, 14))
            hbar(img, 0, 25, int(frac * 63), 25, dimc(OG, 0.7))
        elif st.get("holiday"):
            RT(img, 63, 28, "HOLIDAY", AMB)


# ================================================================== news
class News64(S64):
    col = AMB
    LINES = 7

    @staticmethod
    @lru_cache(maxsize=256)
    def _lines(title: str) -> tuple:
        return tuple(wrap(title, 64, 40)) or ("",)

    def items(self, v):
        return (v.get("items") or [])[: SC.SCREENS[self.key].MAX_ITEMS]

    def loop(self, v):
        per = SC.SCREENS[self.key].per
        return per * max(1, len(self.items(v))) if v else per

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        its = self.items(v)
        tag = v.get("tag") or scr.short
        tc = S2.TAGC.get(tag, AMB)
        if not its:
            header(img, tag, tc)
            T(img, 0, 20, "NO HEADLINES", GREY)
            return
        i = ((k + scr.per // 2) // scr.per) % len(its)
        it = its[i]
        header(img, tag, tc, f"{i + 1}/{len(its)}", DIM)
        ln = self._lines(it["title"])
        for j, line in enumerate(ln[: self.LINES]):
            T(img, 0, 11 + 6 * j, line, CREAM if j == 0 else WHITE)
        if len(ln) > self.LINES:
            put(img, 62, 11 + 6 * self.LINES - 2, AMB)
        h = scr.head(v, it)
        hbar(img, 0, 55, 63, 55, dimc(tc, 0.35))
        if h:
            T(img, 0, 58, fit(h + (" AGO" if not h.endswith("P") else ""), 40), GREY)
        for q in range(len(its)):
            put(img, 63 - 2 * (len(its) - 1 - q), 60, tc if q == i else DIM)


for _k in ("news_npr", "news_world", "news_oregon", "news_tech", "news_arrl", "reddit_ham", "hn", "x_feed"):
    reg(type(f"News64_{_k}", (News64,), {"key": _k}))


# ================================================================== FT8 / FT4
from . import livedata_ft8 as F8  # noqa: E402

MAG, MID = F8.MAG, F8.MID


def ft8_header(img, v, now, right=None, rcol=GREY, title=None):
    band, mode = F8.band_mode(v)
    header(img, title or f"{band} {mode}", AMB, right, rcol)


def ft8_idle(img, v, k, now, what="LISTENING"):
    st = v.get("status") or {}
    ft8_header(img, v, now, f"T-{F8.countdown(v, now):02d}", CREAM)
    if st and st.get("rx_running") is False:
        msg, col = "RX STOPPED", AMB
    elif st and st.get("runtime") and st.get("runtime") not in ("ready", "listening", "running", "decoding", "idle"):
        msg, col = fit(st["runtime"], 60), AMB
    elif st and st.get("clock_ok") is False:
        msg, col = "NO CLOCK", AMB
    else:
        msg, col = what, OG2
    d = ImageDraw.Draw(img)
    cx, cy = 32, 28
    for i in range(4):                                    # listening ripples
        r = 4 + 4 * ((i + k) % 4)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=dimc(col, 0.8 - 0.18 * ((i + k) % 4)))
    put(img, cx, cy, CREAM)
    CT(img, 45, msg, col)
    last = v.get("latest")
    CT(img, 54, f"LAST {LF.ago(last, now)}" if last else "NO DECODES", GREY)


def dec_row(img, y, d, mycall, on=True):
    sn = F8.snr_txt(d["snr"])
    x = T(img, 0, y, sn, F8.snr_col(d["snr"]) if on else dimc(F8.snr_col(d["snr"]), 0.5))
    x = max(x + 2, 13)
    if d["kind"] == "CQ":
        x = T(img, x, y, "CQ", AMB if on else DAMB) + 2
    call = d.get("call") or d["msg"]
    cc = F8.call_col(d, mycall) if on else GREY
    x = T(img, x, y, fit(call, 63 - x), cc)
    return x


@reg
class Ft8Ticker64(S64):
    key, title, col = "ft8_ticker", "FT8 TICKER", AMB

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        if scr.quiet(v) or not v.get("ticker"):
            return ft8_idle(img, v, k, now)
        n = len(v.get("last_slot") or [])
        ft8_header(img, v, now, f"{n} {F8.utc_hm(now)}", CREAM)
        mc = v.get("mycall") or ""
        for i, d in enumerate((v.get("ticker") or [])[:5]):
            y = 11 + 7 * i
            x = dec_row(img, y, d, mc)
            loc = d.get("loc") or {}
            dist = (loc.get("dist") or "").replace("MI", "M").replace("KM", "K")
            if dist and x + 4 + text_width(dist) <= 64:
                RT(img, 63, y, dist, MID)
        hbar(img, 0, 46, 63, 46, (30, 24, 8))
        marquee64(img, F8.ticker_segs(v), k, 48)

    def loop(self, v):
        if not v or SC.SCREENS[self.key].quiet(v) or not v.get("ticker"):
            return 4
        return S2.marquee_len(F8.ticker_segs(v))


@reg
class Ft8Cq64(S64):
    key, title, col = "ft8_cq", "FT8 CQ", AMB

    def draw(self, img, v, k, now):
        cq = (v.get("cq") or [])[:8]
        scr = SC.SCREENS[self.key]
        if not cq:
            if scr.quiet(v):
                return ft8_idle(img, v, k, now, "NO CQ")
            ft8_header(img, v, now, f"T-{F8.countdown(v, now):02d}", DIM)
            UC(img, 20, "NO CQ", AMB)
            CT(img, 44, "LAST 10 MIN", DIM)
            return
        p = page_of(k, len(cq), scr.per)
        d = cq[p]
        loc = d.get("loc") or {}
        mc = v.get("mycall") or ""
        ft8_header(img, v, now, f"CQ {p + 1}/{len(cq)}", AMB)
        T(img, 0, 13, "CQ", AMB if (k % 2 == 0 or d.get("mine")) else DAMB)
        sn = F8.snr_txt(d["snr"])
        RT(img, 63, 13, sn, F8.snr_col(d["snr"]))
        call = d["call"]
        if big_w(call) <= 64:
            UC(img, 17, call, F8.call_col(d, mc))
        else:
            CT(img, 22, call, F8.call_col(d, mc))
        T(img, 0, 34, fit(loc.get("place") or loc.get("country") or "?", 64), LPB)
        dist = f"{loc.get('dist', '')} {loc.get('dir', '')}".strip()
        if dist:
            T(img, 0, 40, dist, MID)
        if d.get("grid"):
            RT(img, 63, 40, d["grid"], OG)
        hbar(img, 0, 47, 63, 47, (30, 24, 8))
        others = [cq[(p + j) % len(cq)] for j in range(1, min(len(cq), 3))]
        for j, o in enumerate(others):
            y = 50 + 7 * j
            T(img, 0, y, fit(o["call"], 34), dimc(CREAM, 0.6))
            RT(img, 63, y, F8.snr_txt(o["snr"]), dimc(F8.snr_col(o["snr"]), 0.7))


@reg
class Ft8Snr64(S64):
    key, title, col = "ft8_snr", "FT8 SNR", AMB

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        bs = scr.bars(v)
        if not bs:
            return ft8_idle(img, v, k, now)
        sel = page_of(k, len(bs), scr.per)
        ft8_header(img, v, now, f"{len(bs)} LAST", CREAM)
        top, bot = 11, 43
        h0 = lambda s: max(1, min(bot - top + 1, int(round((s + 25) / 35 * (bot - top + 1)))))
        for x in range(0, 64, 2):
            put(img, x, bot + 1 - h0(0), DIM)
        d = ImageDraw.Draw(img)
        bw = max(2, min(6, 64 // max(1, len(bs))))
        for i, b in enumerate(bs):
            hh = h0(b["snr"])
            col = F8.snr_col(b["snr"])
            x0 = i * bw
            d.rectangle((x0, bot + 1 - hh, x0 + bw - 2, bot), fill=col if i != sel else WHITE)
        b = bs[sel]
        loc = b.get("loc") or {}
        U(img, 0, 44, fit(b["call"] or b["msg"], 64) if big_w(b["call"] or "") <= 46 else "", F8.call_col(b, v.get("mycall") or ""))
        if big_w(b["call"] or "") > 46:
            T(img, 0, 49, fit(b["call"] or b["msg"], 46), F8.call_col(b, v.get("mycall") or ""))
        RT(img, 63, 49, f"{F8.snr_txt(b['snr'])}DB", F8.snr_col(b["snr"]))
        T(img, 0, 58, fit(loc.get("place") or loc.get("country") or f"{b['hz']}HZ", 64), LPB)


@reg
class Ft8Audio64(S64):
    key, title, col = "ft8_audio", "FT8 AUDIO", AMB
    ROWS = 10

    @staticmethod
    def hx(hz):
        return max(0, min(63, int(hz * 64 / 3000)))

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        lab = scr.label_list(v)
        if not lab and scr.quiet(v):
            return ft8_idle(img, v, k, now)
        ft8_header(img, v, now, "0-3KHZ", GREY)
        slot = int(v.get("slot_s") or 15)
        latest = v.get("latest")
        cyc = [c for c, _ in (v.get("cycles") or [])][: self.ROWS]
        if latest is not None:
            cyc = [latest - i * slot for i in range(self.ROWS)]
        by: dict[int, list] = {}
        for d in v.get("decodes") or []:
            by.setdefault(d["utc"], []).append(d)
        sel = lab[page_of(k, len(lab), scr.per)] if lab else None
        for r, c in enumerate(cyc):
            fade = 1.0 if r == 0 else max(0.22, 0.85 - 0.07 * r)
            for d in by.get(c, []):
                col = tuple(int(x * fade) for x in F8.snr_col(d["snr"]))
                if sel is not None and d is sel and k % 2 == 0:
                    col = WHITE
                x = self.hx(d["hz"])
                hbar(img, x, 10 + 3 * r, min(63, x + 1), 11 + 3 * r, col)
        for hz in range(0, 3001, 500):
            put(img, min(63, self.hx(hz)), 41, GREY if hz % 1000 == 0 else DIM)
        for i, lab_ in enumerate(("0", "1K", "2K", "3K")):
            x = min(63 - text_width(lab_) + 1, max(0, self.hx(i * 1000) - text_width(lab_) // 2))
            T(img, x, 43, lab_, DIM)
        if sel is None:
            CT(img, 54, "QUIET BAND", GREY)
            return
        x = T(img, 0, 51, f"{sel['hz']}HZ", AMB)
        RT(img, 63, 51, F8.snr_txt(sel["snr"]), F8.snr_col(sel["snr"]))
        T(img, 0, 58, fit(sel["call"] or sel["msg"], 64), CREAM)


@reg
class Ft8Cycle64(S64):
    key, title, col = "ft8_cycle", "FT8 CYCLE", AMB

    def draw(self, img, v, k, now):
        slot = v.get("slot_s") or 15.0
        n = len(v.get("last_slot") or [])
        cd = F8.countdown(v, now)
        ft8_header(img, v, now, f"T-{cd:02d}", CREAM if cd > 3 else AMB)
        s = f"{n}"
        x = U2(img, 0, 8, s, CREAM if n else GREY)
        T(img, max(x + 2, 34), 18, "DECODES", GREY)
        T(img, max(x + 2, 34), 25, "LAST SLOT", DIM)
        hist = [c for _, c in (v.get("cycles") or [])][::-1][-21:]
        mx = max(hist + [1])
        d = ImageDraw.Draw(img)
        top, bot = 40, 54
        for i, c in enumerate(hist):
            x0 = i * 3
            if not c:
                put(img, x0, bot, DIM)
                continue
            hh = max(1, int(round(c / mx * (bot - top + 1))))
            d.rectangle((x0, bot + 1 - hh, x0 + 1, bot), fill=CREAM if i == len(hist) - 1 else OG)
        frac = (now % slot) / slot
        even = int(now // slot) % 2 == 0
        hbar(img, 0, 58, 63, 60, DPB if even else DAMB)
        if frac > 0:
            hbar(img, 0, 58, int(frac * 63), 60, PB if even else AMB)
        if SC.SCREENS[self.key].quiet(v):
            put(img, 63, 12, AMB if k % 2 == 0 else DAMB)


NA_BOX64 = (-128.0, -62.0, 20.0, 56.0)


@lru_cache(None)
def na64():
    return Image.open(ROOT / "assets" / "na_land_64x40.png").convert("L")


def na64_xy(lat, lon):
    lo0, lo1, la0, la1 = NA_BOX64
    if not (lo0 <= lon < lo1 and la0 <= lat < la1):
        return None
    return int((lon - lo0) / (lo1 - lo0) * 64), int((la1 - lat) / (la1 - la0) * 40)


@reg
class Ft8Map64(S64):
    key, title, col = "ft8_map", "FT8 MAP", AMB

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        located = v.get("located") or []
        if not located and scr.quiet(v):
            return ft8_idle(img, v, k, now)
        world = page_of(k, 2, scr.per) == 1
        latest = v.get("latest") or 0
        home = v.get("home") or ""
        try:
            hl = F8.FT.G.grid_latlon(home)
        except ValueError:
            hl = LF.HOME
        recent = [d for d in located if d["utc"] >= latest - 900]
        mc = v.get("mycall") or ""
        if world:
            n_c = len({d['loc'].get('dxcc') for d in recent if d['loc'].get('dxcc')})
            ft8_header(img, v, now, f"WORLD {n_c}C", LPB)
            img.paste(Image.new("RGB", (64, 32), (0, 6, 22)), (0, BODY))
            img.paste(Image.new("RGB", (64, 32), F8.LAND), (0, BODY), world64())
            for d in reversed(recent):
                x, y = wxy(d["loc"]["lat"], d["loc"]["lon"])
                put(img, x, BODY + y, F8.snr_col(d["snr"]) if d["utc"] != latest or k % 2 == 0 else WHITE)
            if hl:
                hx, hy = wxy(*hl)
                put(img, hx, BODY + hy, LPB)
            far = sorted(recent, key=lambda d: -(d["loc"].get("km") or 0))
            if far:
                f = far[0]
                T(img, 0, 45, "FARTHEST", GREY)
                T(img, 0, 51, fit(f["call"], 64), F8.call_col(f, mc))
                T(img, 0, 58, fit(f["loc"].get("place") or f["loc"].get("country") or "", 40), LPB)
                RT(img, 63, 58, fit(f["loc"].get("dist") or "", 24), MID)
            return
        inside = [(d, na64_xy(d["loc"]["lat"], d["loc"]["lon"])) for d in recent if d["loc"].get("src") == "grid"]
        inside = [(d, p) for d, p in inside if p]
        ft8_header(img, v, now, f"NA {len(inside)}", LPB)
        img.paste(Image.new("RGB", (64, 40), (0, 6, 22)), (0, BODY - 1))
        img.paste(Image.new("RGB", (64, 40), F8.LAND), (0, BODY - 1), na64())
        hp = na64_xy(*hl) if hl else None
        for d, (x, y) in reversed(inside):
            c = F8.snr_col(d["snr"]) if d["utc"] != latest or k % 2 == 0 else WHITE
            put(img, x, BODY - 1 + y, c)
            if hp and d["utc"] == latest and k % 2 == 0:
                pass
        if hp:
            c = LPB if k % 2 == 0 else PB
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                put(img, hp[0] + dx, BODY - 1 + hp[1] + dy, c if (dx, dy) == (0, 0) else dimc(c, 0.5))
        if inside:
            f = max(inside, key=lambda t: t[0]["loc"].get("km") or 0)[0]
            T(img, 0, 52, fit(f["call"], 36), F8.call_col(f, mc))
            RT(img, 63, 52, fit((f["loc"].get("dist") or ""), 26), MID)
            T(img, 0, 58, fit(f["loc"].get("place") or "", 64), LPB)


@reg
class Ft8Top64(S64):
    key, title, col = "ft8_top", "FT8 TOP", AMB

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        calls = (v.get("calls") or [])[: scr.MAXC]
        if not calls:
            return ft8_idle(img, v, k, now)
        np_ = scr.pages(v)
        p = page_of(k, np_, scr.per)
        band, _ = F8.band_mode(v)
        header(img, f"{band} TOP", AMB, f"{v.get('unique', len(calls))} STNS", CREAM)
        mc = v.get("mycall") or ""
        for i, c in enumerate(calls):
            y = 11 + 6 * i + (i // 2)
            on = i // scr.ROWS == p
            if on:
                hbar(img, 0, y - 1, 63, y + 5, (32, 22, 4))
            T(img, 0, y, f"{i + 1}", GREY if on else DIM)
            sn = F8.snr_txt(c["best"])
            n = f"X{c['n']}"
            xr = 63 - text_width(sn) - 2 - text_width(n)
            T(img, 6, y, fit(c["call"], xr - 8), F8.call_col(c["d"], mc) if on else GREY)
            T(img, xr, y, n, AMB if on else DAMB)
            RT(img, 63, y, sn, F8.snr_col(c["best"]) if on else dimc(F8.snr_col(c["best"]), 0.5))


@reg
class Ft8Alert64(S64):
    key, title, col = "ft8_alert", "FT8 ALERT", AMB

    def draw(self, img, v, k, now):
        scr = SC.SCREENS[self.key]
        al = (v.get("alerts") or [])[: scr.MAXA]
        mc = v.get("mycall") or ""
        if not al:
            header(img, "FT8 ALERTS", OG, "WATCHING", GREY)
            icon2(img, 20, 12, lambda t: SC.check_icon(t, 0, 2, k))
            CT(img, 40, "NO ALERTS", OG2)
            CT(img, 48, f"{v.get('grids', 0)} GRIDS {v.get('dxcc', 0)} DXCC", CREAM)
            CT(img, 56, f"WATCH {mc}" if mc else "SET MY CALL", GREY if mc else DIM)
            return
        p = page_of(k, len(al), scr.per)
        a = al[p]
        d = a["d"]
        loc = d.get("loc") or {}
        label, col = F8.ALERT_STYLE.get(a["kind"], (a["kind"], AMB))
        if a["kind"] == "MYCALL":
            label = "YOU HEARD" if d.get("call") == mc else "CALLING YOU" if d.get("to") == mc else "YOUR CALL"
        flash = k % 2 == 0
        header(img, label, col if (flash or a["kind"] != "MYCALL") else MAG, LF.ago(a["at"], now), GREY)
        if a["kind"] == "MYCALL" and flash:
            ImageDraw.Draw(img).rectangle((0, 0, 63, 8), outline=MAG)
        call = d["call"] or d["msg"]
        if big_w(call) <= 64:
            UC(img, 10, call, F8.call_col(d, mc))
        else:
            CT(img, 15, fit(call, 64), F8.call_col(d, mc))
        sn = F8.snr_txt(d["snr"])
        T(img, 0, 29, f"{sn}DB", F8.snr_col(d["snr"]))
        if d.get("grid"):
            RT(img, 63, 29, d["grid"][:6], OG)
        if a["kind"] == "MYCALL" and d.get("call") != mc:
            for i, line in enumerate(wrapn(d["msg"], 64, 3)):
                T(img, 0, 37 + 6 * i, line, LPB)
        else:
            for i, line in enumerate(wrapn(loc.get("place") or loc.get("country") or "", 64, 2)):
                T(img, 0, 37 + 6 * i, line, LPB)
            dist = f"{loc.get('dist', '')} {loc.get('dir', '')}".strip()
            if dist:
                T(img, 0, 50, dist, MID)
        if len(al) > 1:
            dots(img, len(al), p, 60, col)


# ================================================================== render
def loop_len(key: str, feed) -> int:
    if getattr(SC.SCREENS.get(key), "adaptive", False):        # draws natively at 64x64 too
        return SC.loop_len(key, feed, (64, 64))
    v = feed.value
    lay = DRAW.get(key)
    if lay is None:
        return SC.loop_len(key, feed)
    return max(1, lay.loop(v) if v is not None else SC.SCREENS[key].loop(None))


def render(key: str, feed, k: int = 0, now: float | None = None) -> Image.Image:
    scr = SC.SCREENS[key]
    now = time.time() if now is None else now
    img = Image.new("RGB", SIZE)
    v = feed.value
    lay = DRAW.get(key)
    if v is None:
        title = lay.title if lay and lay.title else scr.short
        header(img, title or scr.short, lay.col if lay else LPB)
        hint = getattr(feed, "hint", None)
        if hint:                                     # SetupNeeded: big words, then the small detail
            rows, cur = [], ""
            for w in hint[0].split():
                if cur and big_w(cur + " " + w) > 62:
                    rows.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w).strip()
            rows = (rows + [cur])[:3] if cur else rows[:3]
            for i, ln in enumerate(rows):
                UC(img, 11 + 14 * i, ln, AMB)
            for i, ln in enumerate(wrapn(hint[1], 64, 2)):
                CT(img, 13 + 14 * len(rows) + 6 * i, ln, DIM)
        elif getattr(feed, "needs_key", False):
            UC(img, 12, "SET", AMB)
            UC(img, 26, "TOKEN", AMB)
            if feed.key_env:
                CT(img, 44, fit(feed.key_env, 64), DIM)
        elif feed.attempt_at is None:
            UC(img, 20, "LOADING", GREY)
            for i in range(3):
                put(img, 28 + 4 * i, 44, LPB if i < k % 4 else DIM)
        else:
            UC(img, 18, "NO DATA", AMB)
            for i, line in enumerate(wrapn((feed.error or "").split(":")[0], 64, 3)):
                T(img, 0, 40 + 6 * i, line, DIM)
        return img
    if lay is None:                                  # not curated yet: letterbox the 64x16 frame
        small = SC.render(key, feed, k, now, size=(64, 16))
        img.paste(small, (0, 24))
        return img
    try:
        lay.draw(img, v, k % loop_len(key, feed), now)
    except Exception as e:  # noqa: BLE001  (a surprising payload must not kill the stream)
        img = Image.new("RGB", SIZE)
        header(img, scr.short, LPB)
        T(img, 0, 20, fit("ERR " + type(e).__name__, 64), RED)
    if feed.stale:
        for dx, dy in ((0, 0), (-1, 0), (0, 1), (-1, 1)):
            img.putpixel((63 + dx, dy), STALE)
    return img


_ = (SKY, OG, PB)
