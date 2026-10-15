"""The Live Data screens (15 here + markets/news in livedata_screens2.py): feed value -> 64x16 frame (orcpixels/livedata.py streams them).

Look: small icon (left) + a big value + 3x5 detail lines, Oregon Radio Company palette
(Oregon green, Pacific blue, amber). Animation is a loop of `loop(value)` frames indexed
by k (one frame per stream tick, ~1 s): icons cycle in 2 or 4 phases, detail pages flip
every `per` frames with the page boundary offset by half a page, so frame loop-1 -> 0 is
an ordinary in-page step (tests/test_livedata.py checks every screen with
orcpixels.loopmetric). A red dot in the top-right corner = stale (source down: last good
value shown).
"""
from __future__ import annotations

import math
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import place as PLACE
from .sources import livefeeds as LF
from .tinyfont import draw_text, text_width

W, H = 64, 16
ROOT = Path(__file__).resolve().parents[1]
OG, OG2, DOG = (40, 210, 90), (140, 245, 160), (12, 80, 34)
PB, LPB, DPB = (30, 140, 255), (120, 200, 255), (0, 38, 90)
AMB, DAMB, CREAM = (255, 168, 20), (120, 64, 0), (255, 236, 190)
RED, DRED, GREY, DIM = (255, 50, 40), (110, 18, 12), (100, 106, 120), (45, 50, 58)
WHITE, SKY = (225, 238, 255), (90, 170, 255)
STALE = (255, 40, 30)


@lru_cache(None)
def _uni():
    return ImageFont.truetype(str(ROOT / "orcpixels" / "fonts" / "unifont.otf"), 16)


def big_w(s: str) -> int:
    return int(_uni().getlength(s))


def big(img, x, y, s, color) -> int:
    """Unifont 8x16 text (digits ~10 px tall). Returns end x."""
    m = Image.new("L", img.size)
    ImageDraw.Draw(m).text((x, y), s, fill=255, font=_uni())
    img.paste(Image.new("RGB", img.size, color), (0, 0), m.point(lambda v: 255 if v > 128 else 0))
    return x + big_w(s)


def txt(img, x, y, s, color) -> int:
    return draw_text(img, x, y, s, color)


def rtxt(img, xr, y, s, color):
    """Right-aligned 3x5 text ending at column xr."""
    return draw_text(img, xr - text_width(s) + 1, y, s, color)


def fit(s: str, px: int) -> str:
    s = s.upper()
    while s and text_width(s) > px:
        s = s[:-1]
    return s.rstrip()


def wrap(s: str, px: int, lines: int) -> list[str]:
    out, cur = [], ""
    for w in s.upper().split():
        cand = (cur + " " + w).strip()
        if text_width(cand) <= px:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = fit(w, px)
        if len(out) >= lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    return out[:lines]


def page_of(k: int, pages: int, per: int) -> int:
    """Page shown at frame k; boundaries offset by per//2 so the loop seam is mid-page."""
    return ((k + per // 2) // per) % pages if pages > 1 else 0


def put(img, x, y, c):
    if 0 <= x < W and 0 <= y < H:
        img.putpixel((int(x), int(y)), c)


def dimc(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


# ------------------------------------------------------------------ icons (12x12)
def cond_of(text: str, night: bool = False) -> str:
    t = (text or "").lower()
    for keys, c in ((("thunder", "tstorm"), "storm"), (("snow", "sleet", "flurr", "ice"), "snow"),
                    (("rain", "shower", "drizzle"), "rain"), (("smoke",), "smoke"),
                    (("fog", "mist", "haze"), "fog"), (("partly", "few", "scattered", "mostly sunny", "mostly clear"), "pcloudy"),
                    (("cloud", "overcast"), "cloudy")):
        if any(k in t for k in keys):
            return c
    return "clear"


SHORT = {"storm": "TSTORM", "snow": "SNOW", "rain": "RAIN", "smoke": "SMOKE", "fog": "FOG",
         "pcloudy": "PT CLDY", "cloudy": "CLOUDY", "clear": "CLEAR"}


def _sun(d, x, y, k, r=3):
    cx, cy = x + 6, y + 6
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=AMB)
    rays = [(0, -5), (0, 5), (-5, 0), (5, 0)] if k % 2 == 0 else [(-4, -4), (4, 4), (-4, 4), (4, -4)]
    for dx, dy in rays:
        d.point((cx + dx, cy + dy), fill=CREAM)


def _moon(d, x, y, k):
    d.ellipse((x + 2, y + 2, x + 10, y + 10), fill=CREAM)
    d.ellipse((x + 5, y + 1, x + 12, y + 8), fill=(0, 0, 0))
    d.point((x + 10, y + 10) if k % 2 == 0 else (x + 11, y + 2), fill=LPB)


def _cloud(d, x, y, col=(150, 165, 185)):
    d.ellipse((x + 1, y + 5, x + 6, y + 10), fill=col)
    d.ellipse((x + 4, y + 3, x + 10, y + 9), fill=col)
    d.rectangle((x + 3, y + 7, x + 11, y + 10), fill=col)


def wx_icon(img, x, y, cond: str, night: bool, k: int):
    d = ImageDraw.Draw(img)
    if cond == "clear":
        (_moon if night else _sun)(d, x, y, k)
    elif cond == "pcloudy":
        if night:
            _moon(d, x - 1, y - 2, k)
        else:
            _sun(d, x - 2, y - 3, k, r=2)
        _cloud(d, x + 1, y + 2)
    elif cond in ("cloudy", "fog", "smoke"):
        if cond == "cloudy":
            _cloud(d, x, y + 1, (170, 180, 200)); _cloud(d, x - 1, y - 2, (110, 120, 140))
        else:
            col = (150, 150, 150) if cond == "fog" else (150, 110, 70)
            for i, yy in enumerate((y + 3, y + 6, y + 9)):
                off = (k + i) % 4
                d.line((x + off, yy, x + off + 8, yy), fill=col)
    else:
        _cloud(d, x, y - 1)
        if cond == "storm":
            d.line([(x + 7, y + 8), (x + 5, y + 10), (x + 7, y + 10), (x + 5, y + 12)],
                   fill=AMB if k % 2 == 0 else CREAM)
        for i, dx in enumerate((3, 6, 9)):
            yy = y + 10 + (k + i) % 4 // 2 * 1 + ((k + i) % 2)
            c = WHITE if cond == "snow" else SKY
            put(img, x + dx, min(H - 1, yy), c)


def warn_icon(img, x, y, k, col):
    d = ImageDraw.Draw(img)
    d.polygon([(x + 6, y), (x, y + 11), (x + 12, y + 11)], outline=col)
    if k % 2 == 0:
        d.line((x + 6, y + 4, x + 6, y + 7), fill=CREAM); d.point((x + 6, y + 9), fill=CREAM)


def check_icon(img, x, y, k):
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + 11, y + 11), radius=3, outline=OG)
    d.line([(x + 3, y + 6), (x + 5, y + 8), (x + 9, y + 3)], fill=OG2 if k % 2 == 0 else OG)


def gauge_col(aqi: int):
    for lim, c, name in ((50, OG, "GOOD"), (100, (240, 220, 40), "MODERATE"), (150, AMB, "SENSITIVE"),
                         (200, RED, "UNHEALTHY"), (300, (190, 60, 220), "VERY UNHL"), (10 ** 6, (150, 20, 40), "HAZARDOUS")):
        if aqi <= lim:
            return c, name
    return RED, "?"


# ------------------------------------------------------------------ screen base
class Screen:
    key = ""
    group = "Weather, sky + radio"
    short = ""           # 3x5 title for the no-data frame
    per = 4              # frames per detail page (multiple of every icon loop: 2 / 4)
    feed = LF.Feed

    def pages(self, v: dict) -> int:
        return 1

    def loop(self, v: dict | None) -> int:
        return self.per * (self.pages(v) if v else 1)

    def draw(self, img, v: dict, k: int, now: float):
        raise NotImplementedError


class WxNow(Screen):
    key, short, feed = "wx_now", PLACE.Lbl(lambda: f"{PLACE.home_tag()} WX"), LF.NwsNow

    def pages(self, v):
        return 3

    def draw(self, img, v, k, now):
        cond = cond_of(v.get("desc", ""), v.get("night"))
        wx_icon(img, 0, 2, cond, v.get("night"), k)
        t = v.get("temp_f")
        tc = LPB if t is not None and t < 45 else (AMB if t is not None and t >= 85 else CREAM)
        x = big(img, 13, 0, "--" if t is None else f"{t}", tc)
        txt(img, x, 1, "\u00b0", tc)
        wind = "CALM" if not v.get("wind_mph") else f"{v.get('wind_dir', '')} {v['wind_mph']}MPH"
        if text_width(wind) > 29:
            wind = f"{v.get('wind_dir', '')}{v['wind_mph']}MPH"
        if text_width(wind) > 29:
            wind = f"{v.get('wind_dir', '')}{v['wind_mph']}"
        rtxt(img, 63, 1, fit(wind, 29), LPB)
        p = page_of(k, 3, self.per)
        line = [SHORT[cond], f"RH {v['rh']}%" if v.get("rh") is not None else "RH --",
                f"{v['pressure_mb']}MB" if v.get("pressure_mb") else PLACE.home_tag()][p]
        rtxt(img, 63, 9, fit(line, 26), OG2 if p == 0 else GREY)


class Forecast(Screen):
    key, short, feed = "forecast", "FORECAST", LF.NwsForecast

    def pages(self, v):
        return max(1, min(4, len(v.get("periods") or [])))

    def draw(self, img, v, k, now):
        pers = v["periods"]
        q = pers[page_of(k, self.pages(v), self.per) % len(pers)]
        wx_icon(img, 0, 2, cond_of(q["short"]), not q["day"], k)
        name = q["name"].upper().replace("THIS ", "").replace("NIGHT", "NT").replace("AFTERNOON", "PM")
        for full, ab in (("MONDAY", "MON"), ("TUESDAY", "TUE"), ("WEDNESDAY", "WED"), ("THURSDAY", "THU"),
                         ("FRIDAY", "FRI"), ("SATURDAY", "SAT"), ("SUNDAY", "SUN"), ("TONT", "TONIGHT")):
            name = name.replace(full, ab)
        tmp = f"{q['temp']}\u00b0" if q.get("temp") is not None else ""
        txt(img, 14, 0, fit(name, 48 - text_width(tmp) - 3), AMB)
        rtxt(img, 63, 0, tmp, CREAM if q["day"] else LPB)
        for i, line in enumerate(wrap(q["short"], 49, 2)):
            txt(img, 14, 6 + 5 * i + i, line, OG2 if i == 0 else OG)


class Alerts(Screen):
    key, short, feed = "alerts", "OR ALERTS", LF.NwsAlerts
    per = 6

    def pages(self, v):
        return max(1, min(5, v.get("count") or 0))

    def draw(self, img, v, k, now):
        al = v.get("alerts") or []
        if not al:
            check_icon(img, 0, 2, k)
            txt(img, 15, 2, f"{PLACE.alerts_tag()} NWS".strip(), LPB)
            txt(img, 15, 9, "NO ALERTS", OG2)
            return
        i = page_of(k, self.pages(v), self.per) % len(al)
        a = al[i]
        col = {"Extreme": RED, "Severe": RED, "Moderate": AMB}.get(a["severity"], (240, 220, 40))
        warn_icon(img, 0, 2, k, col)
        lines = wrap(a["event"], 49, 2)
        for j, line in enumerate(lines):
            txt(img, 14, j * 6, line, col if j == 0 else dimc(col, 0.8))
        y = 11 if len(lines) > 1 else 6
        until = ("TIL " + LF.hm12(a["ends"])) if a.get("ends") else fit(a.get("area", ""), 34)
        txt(img, 14, y, fit(until, 34), GREY)
        rtxt(img, 63, 11, f"{i + 1}/{len(al)}", DIM if len(al) == 1 else LPB)
        if y == 6:
            txt(img, 14, 11, fit(a.get("area", ""), 34), DIM)


class Aqi(Screen):
    key, short, feed = "aqi", PLACE.Lbl(lambda: f"AIR {PLACE.home_tag()}"), LF.AirQuality

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        a = int(v["aqi"])
        col, name = gauge_col(a)
        x = big(img, 0, -1, str(a), col)
        w = max(1, min(x, int(round(x * min(a, 300) / 300))))
        ImageDraw.Draw(img).line((0, 15, x - 1, 15), fill=dimc(col, 0.3))
        ImageDraw.Draw(img).line((0, 15, w - 1, 15), fill=col)
        if k % 2 == 0:
            put(img, w - 1, 15, CREAM)
        xr = max(x + 3, 27)
        txt(img, xr, 0, f"AQI {PLACE.home_tag()}", LPB)
        txt(img, xr, 6, fit(name, 63 - xr), col)
        pm = [f"PM2.5 {v['pm25']:.0f}" if v.get("pm25") is not None else "PM2.5 --",
              f"PM10 {v['pm10']:.0f}" if v.get("pm10") is not None else "PM10 --"][page_of(k, 2, self.per)]
        txt(img, xr, 11, fit(pm, 63 - xr), GREY)


def kp_col(kp):
    return OG if kp < 4 else (AMB if kp < 5 else RED)


class SpaceWxScreen(Screen):
    key, short, feed = "spacewx", "SPACE WX", LF.SpaceWx

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        kps = v["kp"][-8:]
        for i, kp in enumerate(kps):
            h = max(1, min(15, int(round(kp * 1.5)) + 1))
            x = i * 3
            d.rectangle((x, 15 - h, x + 1, 15), fill=dimc(kp_col(kp), 0.75))
            if i == len(kps) - 1:
                d.rectangle((x, 15 - h, x + 1, 15 - h), fill=CREAM if k % 2 == 0 else kp_col(kp))
        if max(kps) < 5:                                        # G1 threshold tick marks
            for x in range(0, 24, 3):
                put(img, x + 2, 15 - 8, DIM)
        cur = kps[-1]
        txt(img, 26, 0, f"KP {cur:.1f}".replace(".0", ""), kp_col(cur))
        g = int(cur) - 4 if cur >= 5 else 0
        state = f"G{g} STORM" if g else ("ACTIVE" if cur >= 4 else "QUIET")
        p = page_of(k, 2, self.per)
        if p == 0:
            line = f"SW {int(v['speed'])}" if v.get("speed") is not None else "SW --"
            lc = AMB if (v.get("speed") or 0) > 500 else LPB
        else:
            bz = v.get("bz")
            line = f"BZ {bz:+.0f}" if bz is not None else "BZ --"
            lc = RED if bz is not None and bz <= -10 else (AMB if bz is not None and bz < -5 else LPB)
        txt(img, 26, 6, line, lc)
        txt(img, 26, 11, state, kp_col(cur))


def xr_col(cls: str):
    return {"A": GREY, "B": OG, "C": AMB, "M": RED, "X": (255, 90, 255)}.get((cls or "?")[0].upper(), GREY)


class XRayScreen(Screen):
    key, short, feed = "xray", "X-RAY", LF.XRay

    def pages(self, v):
        return 2 if v.get("max_class") else 1

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        cls = v["current"]
        col = xr_col(cls)
        cx, cy = 5, 8
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=AMB)
        flare = [(cx + 4, cy - 2), (cx + 5, cy - 3), (cx + 4, cy + 2), (cx - 4, cy - 3)]
        for i, (fx, fy) in enumerate(flare):
            if (k + i) % 4 < 2:
                put(img, fx, fy, col if cls[0] in "CMX" else CREAM)
        p = page_of(k, self.pages(v), self.per)
        if p == 0:
            big(img, 12, 0, cls, col)
            txt(img, 49, 0, "XRAY", DIM)
            if v.get("sfi") is not None:
                txt(img, 49, 6, "SFI", LPB)
                txt(img, 49, 11, f"{v['sfi']:.0f}", LPB)
        else:
            big(img, 12, 0, v["max_class"], xr_col(v["max_class"]))
            txt(img, 49, 0, "LAST", GREY)
            txt(img, 49, 6, "FLR", GREY)
            if v.get("max_time"):
                txt(img, 49, 11, fit(LF.ago(v["max_time"], now), 15), LPB)


BANDS = ["80m-40m", "30m-20m", "17m-15m", "12m-10m"]
CONDC = {"Good": OG, "Fair": AMB, "Poor": RED}


class HfBandsScreen(Screen):
    key, short, feed = "hf_bands", "HF BANDS", LF.HfBands
    per = 6

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        if page_of(k, 2, self.per) == 0:
            day = 7 <= LF.pt(now).hour < 19
            for j, (lab, half) in enumerate((("D", "day"), ("N", "night"))):
                y = 6 + j * 5
                on = (half == "day") == day
                txt(img, 0, y, lab, (CREAM if k % 2 == 0 else AMB) if on else GREY)
                for i, b in enumerate(BANDS):
                    c = CONDC.get(v["bands"].get(b, {}).get(half, ""), DIM)
                    x = 6 + i * 15
                    d.rectangle((x, y, x + 12, y + 3), fill=c if on else dimc(c, 0.55))
            for i, b in enumerate(BANDS):
                txt(img, 6 + i * 15 + 3, 0, b[:2], LPB)
        else:
            txt(img, 0, 0, f"SFI {v['sfi']}", AMB)
            rtxt(img, 63, 0, f"SN {v['sn']}", AMB)
            txt(img, 0, 6, f"A {v['a']}  K {v['k']}", LPB)
            rtxt(img, 63, 6, fit(v.get("xray", ""), 20), xr_col(v.get("xray", "")))
            txt(img, 0, 11, fit(v.get("geomag", ""), 36), OG2)
            rtxt(img, 63, 11, fit(v.get("noise", ""), 26), GREY)


@lru_cache(None)
def world_land():
    return Image.open(ROOT / "assets" / "world_land_32x16.png").convert("L")


def _mx(lat, lon):
    return int((lon + 180) / 360 * 32) % 32, max(0, min(15, int((90 - lat) / 180 * 16)))


def dur(s: float) -> str:
    s = max(0, int(s))
    if s < 60:
        return f"{s}S"
    if s < 3600:
        return f"{s // 60}M"
    return f"{s // 3600}H{(s % 3600) // 60:02d}"


class IssScreen(Screen):
    key, short, feed = "iss", "ISS", LF.Iss

    def pages(self, v):
        return 2 if v.get("pass") else 1

    def draw(self, img, v, k, now):
        img.paste(Image.new("RGB", (32, 16), (0, 6, 22)), (0, 0))
        img.paste(Image.new("RGB", (32, 16), (16, 72, 34)), (0, 0), world_land())
        for la, lo in v.get("track") or []:
            x, y = _mx(la, lo)
            put(img, x, y, DAMB)
        if LF.HOME:
            hx, hy = _mx(*LF.HOME)
            put(img, hx, hy, LPB)
        x, y = _mx(v["lat"], v["lon"])
        put(img, x, y, CREAM if k % 2 == 0 else AMB)
        txt(img, 35, 0, "ISS", AMB)
        if v.get("alt_km"):
            rtxt(img, 63, 0, f"{v['alt_km']:.0f}K", DIM)
        ps = v.get("pass")
        if not ps:
            txt(img, 35, 6, f"{abs(v['lat']):.0f}{'N' if v['lat'] >= 0 else 'S'} {abs(v['lon']):.0f}{'E' if v['lon'] >= 0 else 'W'}", LPB)
            txt(img, 35, 11, "NO PASS", GREY)
            return
        if page_of(k, 2, self.per) == 0:
            if ps["in_progress"] or ps["aos"] <= now <= ps["los"]:
                txt(img, 35, 6, "OVERHEAD", OG2)
                txt(img, 35, 11, f"MAX {ps['max_el']}\u00b0", OG)
            else:
                txt(img, 35, 6, "PASS IN", LPB)
                txt(img, 35, 11, dur(ps["aos"] - now), CREAM)
        else:
            txt(img, 35, 6, fit("AT " + LF.hm12(ps["aos"]), 28), LPB)
            txt(img, 35, 11, f"MAX {ps['max_el']}\u00b0", OG2 if ps["max_el"] >= 40 else GREY)


def mag_col(m):
    return OG if m < 2.5 else (AMB if m < 4.0 else RED)


STATES = {"Oregon": "OR", "Washington": "WA", "California": "CA", "Idaho": "ID", "Nevada": "NV",
          "British Columbia": "BC", "Canada": "CAN"}


def short_place(p: str) -> str:
    for full, ab in STATES.items():
        p = p.replace(full, ab)
    return p.replace(" km ", "KM ").replace(" of ", " OF ")


class QuakeScreen(Screen):
    key, short, feed = "quake", "QUAKES", LF.Quakes

    def pages(self, v):
        return 2 if v.get("quakes") else 1

    def draw(self, img, v, k, now):
        qs = v.get("quakes") or []
        trace = [0, 0, -1, 2, -3, 1, -1, 0]
        for x in range(12):
            a = trace[(x + 2 * k) % 8]
            put(img, x, 8 + a, OG if a == 0 else AMB)
        if not qs:
            txt(img, 15, 2, f"{PLACE.quake_tag()} QUAKES".strip(), LPB)
            txt(img, 15, 9, "NONE M1.5+", OG2)
            return
        q = qs[0]
        col = mag_col(q["mag"])
        if page_of(k, 2, self.per) == 0:
            big(img, 14, 0, f"M{q['mag']:.1f}", col)
            txt(img, 49, 1, PLACE.quake_tag()[:3], DIM)
            txt(img, 49, 9, LF.ago(q["time"], now), LPB)
        else:
            txt(img, 14, 0, f"M{q['mag']:.1f}", col)
            rtxt(img, 63, 0, LF.ago(q["time"], now) + " AGO", GREY)
            for i, line in enumerate(wrap(short_place(q["place"]), 49, 2)):
                txt(img, 14, 6 + 5 * i, line, LPB if i == 0 else OG2)


class AircraftScreen(Screen):
    key, short, feed = "aircraft", "AIRCRAFT", LF.Aircraft

    def pages(self, v):
        return max(1, min(2, len(v.get("closest") or [])))

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        d.ellipse((0, 2, 11, 13), outline=DOG)
        a = k % 4 * math.pi / 2 - math.pi / 2
        d.line((5.5, 7.5, 5.5 + 5 * math.cos(a), 7.5 + 5 * math.sin(a)), fill=OG)
        put(img, 5, 7, CREAM)
        n = v.get("count", 0)
        x = big(img, 14, 0, str(n), CREAM)
        txt(img, x + 1, 9, "AC", GREY)
        cl = v.get("closest") or []
        if not cl:
            txt(img, 40, 6, "NONE", GREY)
            return
        c = cl[page_of(k, self.pages(v), self.per) % len(cl)]
        xr = max(x + 10, 40)
        txt(img, xr, 0, fit(c["call"], 63 - xr), AMB)
        alt = c.get("alt_ft")
        alt_s = "--" if alt is None else (f"FL{alt // 100:03d}" if alt >= 18000 else f"{alt}FT")
        txt(img, xr, 6, fit(alt_s, 63 - xr), LPB)
        txt(img, xr, 11, fit(f"{c['km']:.0f}KM", 63 - xr), GREY)


class TidesScreen(Screen):
    key, short, feed = "tides", "TIDES", LF.Tides

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        ev = v["events"]
        hs = [LF.tide_height(ev, now + (x - 6) * 1800) for x in range(40)]       # -3 h .. +16.5 h
        known = [h for h in hs if h is not None]
        lo, hi = (min(known), max(known)) if known else (0, 1)
        span = max(0.5, hi - lo)
        d = ImageDraw.Draw(img)
        for x, h in enumerate(hs):
            if h is None:
                continue
            y = 15 - int(round((h - lo) / span * 8))
            d.line((x, y + 1, x, 15), fill=DPB)
            put(img, x, y, LPB)
        nowh = hs[6]
        if nowh is not None:
            y = 15 - int(round((nowh - lo) / span * 8))
            put(img, 6, y, CREAM if k % 2 == 0 else AMB)
        txt(img, 0, 0, PLACE.tide_tag(), DIM)
        nxt = [e for e in ev if e["t"] > now][:2]
        if not nxt:
            return
        e = nxt[min(page_of(k, 2, self.per), len(nxt) - 1)]
        txt(img, 43, 0, "HIGH" if e["type"] == "H" else "LOW", AMB if e["type"] == "H" else LPB)
        txt(img, 43, 6, fit(LF.hm12(e["t"]), 21), CREAM)
        txt(img, 43, 11, fit(f"{e['v']:.1f}FT", 21), OG2)


class RiverScreen(Screen):
    key, short, feed = "river", "RIVER", LF.River

    def loop(self, v):
        return 4

    def draw(self, img, v, k, now):
        ser = v["series"]
        n = 36
        pts = [ser[int(i * (len(ser) - 1) / (n - 1))] for i in range(n)] if len(ser) > 1 else ser * n
        lo, hi = min(pts), max(pts)
        span = max(0.3, hi - lo)
        d = ImageDraw.Draw(img)
        for x, h in enumerate(pts):
            y = 15 - int(round((h - lo) / span * 7))
            d.line((x, y + 1, x, 15), fill=DPB)
            put(img, x, y, LPB)
        put(img, n - 1, 15 - int(round((pts[-1] - lo) / span * 7)), CREAM if k % 2 == 0 else AMB)
        for x in range(n):                                          # ripple glint
            if (x + k * 3) % 12 == 0:
                put(img, x, 15, (60, 120, 200))
        txt(img, 0, 0, PLACE.river_tag(), OG2)
        txt(img, 39, 7, f"{v['ft']:.1f}", CREAM)
        txt(img, 39 + text_width(f"{v['ft']:.1f}") + 2, 7, "FT", GREY)
        tr = v.get("trend")
        if tr is not None:
            arrow = "+" if tr > 0.05 else ("-" if tr < -0.05 else "=")
            txt(img, 39, 12 - 1, fit(f"{arrow}{abs(tr):.1f} 3H", 25), OG if arrow == "+" else (AMB if arrow == "-" else GREY))


class SunMoonScreen(Screen):
    key, short, feed = "sunmoon", "SUN MOON", LF.SunMoon
    per = 6

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        if page_of(k, 2, self.per) == 0:
            d.pieslice((0, 4, 11, 15), 180, 360, fill=AMB)
            for i, (dx, dy) in enumerate(((5, 1), (1, 4), (10, 4), (5, 0))):
                if (k + i) % 4 < 2:
                    put(img, dx, dy, CREAM)
            d.line((0, 10, 11, 10), fill=DAMB)
            rise = v["next_rise"] if v.get("next_rise") else v.get("rise")
            txt(img, 14, 0, "RISE " + (LF.hm12(rise) if rise else "--"), AMB)
            txt(img, 14, 6, "SET  " + (LF.hm12(v["set"]) if v.get("set") else "--"), (255, 120, 60))
            if v.get("day_s"):
                ds = int(v["day_s"])
                txt(img, 14, 11, f"DAY {ds // 3600}H{(ds % 3600) // 60:02d}", GREY)
        else:
            m = v["moon"]
            moon_icon(img, 0, 2, m["illum"], m["waxing"])
            if k % 2 == 0:
                put(img, 11, 1, LPB)
            txt(img, 14, 0, "MOON", LPB)
            txt(img, 14, 6, m["name"], CREAM)
            txt(img, 14, 11, f"{m['illum'] * 100:.0f}% LIT", GREY)


def moon_icon(img, x, y, illum: float, waxing: bool, r=5.5):
    cx, cy = x + 5.5, y + 5.5
    for yy in range(12):
        for xx in range(12):
            dx, dy = (xx + 0.5 - 6) / r, (yy + 0.5 - 6) / r
            if dx * dx + dy * dy > 1:
                continue
            half = math.sqrt(max(0.0, 1 - dy * dy))
            t = (1 - 2 * illum) * half                  # terminator x (+half = new, -half = full)
            lit = dx > t if waxing else dx < -t
            img.putpixel((x + xx, y + yy), CREAM if lit else (40, 42, 50))
    _ = cx, cy


class ClockScreen(Screen):
    key, short, feed = "clock", "UTC", LF.UtcClock

    def loop(self, v):
        return 2

    def draw(self, img, v, k, now):
        u = time.gmtime(now)
        p = LF.pt(now)
        hh, mm = f"{u.tm_hour:02d}", f"{u.tm_min:02d}"
        x = big(img, 0, 0, hh, CREAM)
        if k % 2 == 0:
            big(img, x - 2, 0, ":", AMB)
        big(img, x + 4, 0, mm, CREAM)
        ImageDraw.Draw(img).line((0, 15, int(u.tm_sec / 59 * 35), 15), fill=DAMB)
        txt(img, 42, 0, "UTC", AMB)
        txt(img, 42, 6, time.strftime("%b%d", u).upper(), GREY)
        txt(img, 42, 11, f"{p.hour:02d}:{p.minute:02d}", LPB)


class GitHubScreen(Screen):
    key, short, feed = "github", "GITHUB", LF.GitHub

    def pages(self, v):
        return 3

    def draw(self, img, v, k, now):
        d = ImageDraw.Draw(img)
        d.line((3, 2, 3, 13), fill=GREY)
        d.line((3, 9, 8, 5), fill=GREY)
        d.ellipse((1, 11, 5, 15), outline=OG2)
        d.ellipse((1, 0, 5, 4), outline=OG2)
        d.ellipse((7, 3, 11, 7), fill=AMB if k % 2 == 0 else DAMB)
        txt(img, 14, 0, (str(PLACE.get("livedata_github_user") or "GITHUB")).upper()[:12], DIM)
        p = page_of(k, 3, self.per)
        if p == 0:
            txt(img, 14, 6, f"{v['stars']} STARS", AMB)
            txt(img, 14, 11, f"{v['repos']} REPOS", LPB)
        elif p == 1:
            txt(img, 14, 6, fit(v["last"], 49), OG2)
            txt(img, 14, 11, fit("PUSH " + LF.ago(v.get("last_at"), now) + " AGO", 49), GREY)
        else:
            name, stars = (v.get("top") or [("?", 0)])[0]
            txt(img, 14, 6, fit("TOP " + name, 49), CREAM)
            txt(img, 14, 11, f"{stars} STARS", AMB)


SCREENS: dict[str, Screen] = {s.key: s for s in (
    WxNow(), Forecast(), Alerts(), Aqi(), SpaceWxScreen(), XRayScreen(), HfBandsScreen(), IssScreen(),
    QuakeScreen(), AircraftScreen(), TidesScreen(), RiverScreen(), SunMoonScreen(), ClockScreen(), GitHubScreen())}
from . import livedata_screens2 as _S2  # noqa: E402  (markets + news, 2026-10-02 23:13 PT request)
SCREENS.update({s.key: s for s in _S2.SCREENS2 + _S2.OPTIONAL2})
OPTIONAL = {s.key for s in _S2.OPTIONAL2}      # needs a key; listed, never auto-selected
from . import livedata_ft8 as _FT8  # noqa: E402,F401  (FT8/FT4 from OrcSDR; registers itself in SCREENS)
from . import live_plugins as _PLUG  # noqa: E402  (Sports, Entertainment, ...: milestone 2)
_PLUG.load(SCREENS)
from . import live_catalog as _CATALOG  # noqa: E402  (declarative free-source catalog, 2026-10-14)
_CATALOG.load_into(SCREENS)


def make_feeds() -> dict[str, LF.Feed]:
    return {k: s.feed() for k, s in SCREENS.items()}


def loop_len(key: str, feed: LF.Feed, size: tuple[int, int] | None = None) -> int:
    if getattr(SCREENS[key], "adaptive", False):
        return max(1, SCREENS[key].loop_at(feed.value, tuple(size or (W, H))))
    if size is not None and tuple(size) == (64, 64):
        from . import live64
        return live64.loop_len(key, feed)
    return max(1, SCREENS[key].loop(feed.value if feed.value is not None else None))


def render(key: str, feed: LF.Feed, k: int = 0, now: float | None = None,
           size: tuple[int, int] | None = None) -> Image.Image:
    """One frame of screen `key` at loop index k (wrapped to the screen's loop length).
    size=(64, 64) -> the curated 64x64 layout (live64.py); default / (64, 16) -> this set."""
    scr = SCREENS[key]
    if getattr(scr, "adaptive", False):                 # draws natively at any size
        from . import live_adaptive
        return live_adaptive.render(scr, feed, k, time.time() if now is None else now, tuple(size or (W, H)))
    if size is not None and tuple(size) == (64, 64):
        from . import live64
        return live64.render(key, feed, k, now)
    now = time.time() if now is None else now
    img = Image.new("RGB", (W, H))
    v = feed.value
    if v is None:
        txt(img, 1, 1, scr.short, LPB)
        hint = getattr(feed, "hint", None)
        if hint:                                            # SetupNeeded: the headline is the message
            end = txt(img, 1, 9, fit(hint[0], 62), AMB)
            rest = fit(hint[1], 62 - end - 3) if hint[1] else ""
            if rest == hint[1]:                             # detail only when it fits whole
                txt(img, end + 3, 9, rest, DIM)
        elif getattr(feed, "needs_key", False):
            txt(img, 1, 9, "SET TOKEN", AMB)
            if feed.key_env:
                txt(img, 1 + text_width("SET TOKEN") + 3, 9, fit(feed.key_env.split("_")[0], 20), DIM)
        elif feed.attempt_at is None:
            txt(img, 1, 9, "LOADING", GREY)
        else:
            txt(img, 1, 9, "NO DATA", AMB)
            txt(img, 34, 9, fit((feed.error or "").split(":")[0], 29), DIM)
        return img
    try:
        scr.draw(img, v, k % loop_len(key, feed), now)
    except Exception as e:  # noqa: BLE001  (a surprising payload must not kill the stream)
        img = Image.new("RGB", (W, H))
        txt(img, 1, 1, scr.short, LPB)
        txt(img, 1, 9, fit("ERR " + type(e).__name__, 62), RED)
    if feed.stale:
        img.putpixel((W - 1, 0), STALE)
    return img
