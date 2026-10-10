"""FT8 / FT4 Live Data screens (OrcSDR decodes), 2026-10-08 16:22 PT request.

Eight 64x16 views over one shared decode feed (sources/ft8feed.py):
  ft8_ticker  seamless marquee, newest first: "FT8 -12 CQ K7ABC BOISE ID 326MI SE"
              (SNR colour, CQ amber, my call magenta) under a band / decodes / UTC header
  ft8_cq      CQ callers, one per page: call, SNR, place, distance + bearing, grid
  ft8_snr     last cycle as SNR bars (strongest left); each bar named in turn
  ft8_audio   waterfall-style map of audio offsets 0-3 kHz over the last 5 cycles
  ft8_cycle   decodes in the last cycle (big), per-cycle history, live slot countdown
  ft8_map     where they are: North America and world dot maps + farthest station
  ft8_top     band activity: most-heard callers (decode count, best SNR)
  ft8_alert   my call heard / new DXCC / far DX / new grid, flashing; quiet otherwise
Every loop is seamless (marquee period = strip width / step; paged views use page_of).
"""
from __future__ import annotations

import math
import time
from functools import lru_cache

from PIL import Image, ImageDraw

from .livedata_screens import (AMB, CREAM, DAMB, DIM, DOG, DPB, DRED, GREY, H, LPB, OG, OG2, PB, RED, ROOT, W,
                               WHITE, Screen, big, big_w, fit, page_of, put, rtxt, txt, world_land, _mx)
from .livedata_screens2 import STEP, strip
from .sources import ft8feed as FT
from .sources import livefeeds as LF
from .tinyfont import text_width

MAG, DMAG = (255, 70, 210), (90, 10, 70)
SNR_HI, SNR_MID, SNR_LO, SNR_WEAK = (90, 255, 120), (40, 200, 80), (255, 190, 40), (255, 95, 50)
LAND = (16, 72, 34)
MID = (150, 156, 170)          # distances / secondary text (GREY is too dim on the LEDs)
TOP = 5                       # ticker marquee rows 5..15 (header rows 0..4)


def snr_col(s):
    if s is None:
        return GREY
    return SNR_HI if s >= 0 else SNR_MID if s >= -10 else SNR_LO if s >= -17 else SNR_WEAK


def snr_txt(s) -> str:
    return "--" if s is None else f"{s:+d}" if s > 0 else f"{s:d}"


def utc_hm(ts: float) -> str:
    return time.strftime("%H%MZ", time.gmtime(ts))


def band_mode(v) -> tuple[str, str]:
    st = v.get("status") or {}
    return (st.get("band") or "--").upper(), (st.get("mode") or "FT8").upper()


def countdown(v, now) -> int:
    slot = v.get("slot_s") or 15.0
    return int(math.ceil(slot - (now % slot))) % int(math.ceil(slot)) or int(math.ceil(slot))


def header(img, v, now, right: str | None = None, rcol=GREY) -> int:
    band, mode = band_mode(v)
    x = txt(img, 0, 0, band, AMB)
    x = txt(img, x + 2, 0, mode, LPB)
    if right:
        rtxt(img, 63, 0, right, rcol)
    return x


def call_col(d, mycall: str):
    if d.get("mine"):
        return MAG
    return CREAM


def idle(img, v, k, now, what: str = "LISTENING"):
    """Smart idle: why it is quiet (stopped / no clock / just quiet) + next-slot countdown."""
    st = v.get("status") or {}
    header(img, v, now, f"T-{countdown(v, now):02d}", CREAM)
    if st and st.get("rx_running") is False:
        msg, col = "RX STOPPED", AMB
    elif st and st.get("runtime") and st.get("runtime") not in ("ready", "listening", "running", "decoding", "idle"):
        msg, col = fit(st["runtime"], 50), AMB
    elif st and st.get("clock_ok") is False:
        msg, col = "NO CLOCK", AMB
    else:
        msg, col = what, OG2
    band, mode = band_mode(v)
    x = txt(img, 0, 7, f"{mode} {msg}", col)
    for i in range(3):                                   # animated dots
        put(img, x + 1 + 2 * i, 11, col if i < k % 4 else DIM)
    last = v.get("latest")
    rtxt(img, 63, 11, f"LAST {LF.ago(last, now)}" if last else "NO DECODES", GREY)


# ================================================================== 1 ticker
def entry_segs(d, mycall: str) -> tuple:
    loc = d.get("loc") or {}
    segs = [(f"{d['mode']} ", GREY), (snr_txt(d["snr"]) + " ", snr_col(d["snr"]))]
    if d["kind"] == "CQ":
        segs.append(("CQ ", AMB))
    if d.get("call"):
        segs.append((d["call"], call_col(d, mycall)))
        if d.get("to") and d["kind"] != "CQ":
            to_me = bool(mycall) and d["to"] == mycall
            segs.append(((" >" + d["to"]), MAG if to_me else (110, 116, 130)))
        if loc.get("place"):
            segs.append((" " + loc["place"], LPB))
        if loc.get("dist"):
            segs.append((f" {loc['dist']} {loc['dir']}".rstrip(), MID))
    else:
        segs.append((d["msg"], CREAM))
    segs.append(("   ", GREY))
    return tuple(segs)


def ticker_segs(v) -> tuple:
    out = []
    for d in (v.get("ticker") or [])[:8]:
        out += entry_segs(d, v.get("mycall") or "")
    return tuple(out)


@lru_cache(maxsize=16)
def _tstrip(segs: tuple) -> Image.Image:
    return strip(segs, STEP, TOP - 4).crop((0, TOP, strip(segs, STEP, TOP - 4).width, H))


class Ft8Screen(Screen):
    group = "FT8 / FT4"
    feed = FT.Ft8Feed
    per = 4

    def quiet(self, v) -> bool:
        return bool(v.get("idle"))


class TickerScreen(Ft8Screen):
    key, short = "ft8_ticker", "FT8 TICKER"
    feed = FT.feed_class("ft8_ticker", "FT8/FT4 decode ticker")

    def loop(self, v):
        if not v or self.quiet(v) or not v.get("ticker"):
            return 4
        return _tstrip(ticker_segs(v)).width // STEP

    def draw(self, img, v, k, now):
        if self.quiet(v) or not v.get("ticker"):
            return idle(img, v, k, now)
        n = len(v.get("last_slot") or [])
        x = header(img, v, now)
        cnt = f"{n}"
        x2 = txt(img, x + 3, 0, cnt, CREAM)
        tm = utc_hm(now)
        if x2 + 3 + text_width(tm) <= 64:
            rtxt(img, 63, 0, tm, MID)
        s = _tstrip(ticker_segs(v))
        o = (k * STEP) % s.width
        xx = -o
        while xx < W:
            img.paste(s, (xx, TOP))
            xx += s.width


# ================================================================== 2 CQ callers
class CqScreen(Ft8Screen):
    key, short = "ft8_cq", "FT8 CQ"
    feed = FT.feed_class("ft8_cq", "FT8 CQ callers + location")

    def pages(self, v):
        return max(1, min(8, len(v.get("cq") or [])))

    def draw(self, img, v, k, now):
        cq = (v.get("cq") or [])[:8]
        if not cq:
            if self.quiet(v):
                return idle(img, v, k, now, "NO CQ")
            header(img, v, now, f"T-{countdown(v, now):02d}", DIM)
            txt(img, 0, 7, "NO CQ CALLS", AMB)
            rtxt(img, 63, 11, "LAST 10 MIN", DIM)
            return
        p = page_of(k, len(cq), self.per)
        d = cq[p]
        loc = d.get("loc") or {}
        txt(img, 0, 0, "CQ", AMB if (k % 2 == 0 or d.get("mine")) else DAMB)
        sn = snr_txt(d["snr"])
        txt(img, 10, 0, fit(d["call"], 52 - text_width(sn)), call_col(d, v.get("mycall") or ""))
        rtxt(img, 63, 0, sn, snr_col(d["snr"]))
        txt(img, 0, 6, fit(loc.get("place") or loc.get("country") or "?", 64), LPB)
        dist = f"{loc.get('dist', '')} {loc.get('dir', '')}".strip()
        x = txt(img, 0, 11, dist, MID) if dist else 0
        cnt = f"{p + 1}/{len(cq)}"
        gr = d.get("grid") or ""
        if gr and x + 3 + text_width(gr) + 3 + text_width(cnt) <= 64:
            txt(img, x + 3, 11, gr, OG)
        rtxt(img, 63, 11, cnt, DIM)


# ================================================================== 3 SNR bars
class SnrScreen(Ft8Screen):
    key, short = "ft8_snr", "FT8 SNR"
    feed = FT.feed_class("ft8_snr", "FT8 SNR bars (last cycle)")
    per = 2
    MAXB = 13

    def bars(self, v):
        ds = [d for d in (v.get("last_slot") or []) if d.get("snr") is not None]
        return sorted(ds, key=lambda d: -d["snr"])[: self.MAXB]

    def pages(self, v):
        return max(1, len(self.bars(v)))

    def draw(self, img, v, k, now):
        bs = self.bars(v)
        if not bs:
            return idle(img, v, k, now)
        sel = page_of(k, len(bs), self.per)
        h0 = lambda s: max(1, min(16, int(round((s + 25) / 35 * 16))))
        for x in range(0, 26, 2):                       # 0 dB reference
            put(img, x, 16 - h0(0), DIM)
        d = ImageDraw.Draw(img)
        for i, b in enumerate(bs):
            hh = h0(b["snr"])
            col = snr_col(b["snr"])
            d.line((2 * i, 15, 2 * i, 16 - hh), fill=col if i != sel else WHITE)
        b = bs[sel]
        loc = b.get("loc") or {}
        txt(img, 28, 0, fit(b["call"] or b["msg"], 36), call_col(b, v.get("mycall") or ""))
        x = txt(img, 28, 6, f"{snr_txt(b['snr'])}DB", snr_col(b["snr"]))
        rtxt(img, 63, 6, f"{sel + 1}/{len(bs)}", DIM)
        txt(img, 28, 11, fit(loc.get("place") or loc.get("country") or f"{b['hz']}HZ", 36), LPB)


# ================================================================== 4 audio offset map
class AudioScreen(Ft8Screen):
    key, short = "ft8_audio", "FT8 AUDIO"
    feed = FT.feed_class("ft8_audio", "FT8 audio-offset map 0-3 kHz")
    MAXL = 10
    ROWS = 5

    def label_list(self, v):
        return sorted(v.get("last_slot") or [], key=lambda d: d["hz"])[: self.MAXL]

    def pages(self, v):
        return max(1, len(self.label_list(v)))

    @staticmethod
    def hx(hz):
        return max(0, min(63, int(hz * 64 / 3000)))

    def draw(self, img, v, k, now):
        lab = self.label_list(v)
        if not lab and self.quiet(v):
            return idle(img, v, k, now)
        cyc = [c for c, _ in (v.get("cycles") or [])][: self.ROWS]
        by: dict[int, list] = {}
        for d in v.get("decodes") or []:
            by.setdefault(d["utc"], []).append(d)
        latest = v.get("latest")
        if latest is not None and cyc and cyc[0] != latest:     # cycles are counted from "now"
            cyc = [latest - i * int(v.get("slot_s") or 15) for i in range(self.ROWS)]
        sel = lab[page_of(k, len(lab), self.per)] if lab else None
        for r, c in enumerate(cyc):
            fade = 1.0 if r == 0 else max(0.25, 0.8 - 0.15 * r)
            for d in by.get(c, []):
                col = tuple(int(x * fade) for x in snr_col(d["snr"]))
                if sel is not None and d is sel and k % 2 == 0:
                    col = WHITE
                x = self.hx(d["hz"])
                for dx in (0, 1):
                    put(img, x + dx, 2 * r, col)
                    put(img, x + dx, 2 * r + 1, col)
        for hz in range(0, 3001, 500):                  # axis
            put(img, min(63, self.hx(hz)), 10, GREY if hz % 1000 == 0 else DIM)
        for x in range(64):
            if img.getpixel((x, 10)) == (0, 0, 0):
                put(img, x, 10, (18, 22, 28))
        if sel is None:
            txt(img, 0, 11, "QUIET BAND", GREY)
            rtxt(img, 63, 11, "0-3KHZ", DIM)
            return
        x = txt(img, 0, 11, f"{sel['hz']}", AMB)
        sn = snr_txt(sel["snr"])
        x = txt(img, x + 2, 11, fit(sel["call"] or sel["msg"], 62 - x - text_width(sn) - 3), CREAM)
        rtxt(img, 63, 11, sn, snr_col(sel["snr"]))


# ================================================================== 5 cycle count + countdown
class CycleScreen(Ft8Screen):
    key, short = "ft8_cycle", "FT8 CYCLE"
    feed = FT.feed_class("ft8_cycle", "FT8 cycle count + countdown")

    def draw(self, img, v, k, now):
        slot = v.get("slot_s") or 15.0
        n = len(v.get("last_slot") or [])
        cd = countdown(v, now)
        band, mode = band_mode(v)
        col = CREAM if n else GREY
        s = f"{n}"
        big(img, 0, 1, s, col)
        x0 = max(19, big_w(s) + 3)
        x = txt(img, x0, 0, band, AMB)
        txt(img, x + 2, 0, mode, LPB)
        rtxt(img, 63, 0, f"T-{cd:02d}", CREAM if cd > 3 else AMB)
        hist = [c for _, c in (v.get("cycles") or [])][::-1]     # oldest .. newest
        nb = (64 - x0) // 3
        hist = hist[-nb:] if hist else []
        mx = max(hist + [1])
        d = ImageDraw.Draw(img)
        for i, c in enumerate(hist):
            if not c:
                put(img, x0 + 3 * i, 13, DIM)
                continue
            hh = max(1, int(round(c / mx * 7)))
            cc = CREAM if i == len(hist) - 1 else OG
            d.rectangle((x0 + 3 * i, 14 - hh, x0 + 3 * i + 1, 13), fill=cc)
        frac = (now % slot) / slot
        even = int(now // slot) % 2 == 0
        d.line((0, 15, 63, 15), fill=DPB if even else DAMB)
        if frac > 0:
            d.line((0, 15, int(frac * 63), 15), fill=PB if even else AMB)
        if self.quiet(v):
            put(img, 0, 0, AMB if k % 2 == 0 else DAMB)


# ================================================================== 6 location map
NA_BOX = (-135.0, -55.0, 20.0, 60.0)             # lon0, lon1, lat0, lat1 (assets/na_land_40x16.png)


@lru_cache(None)
def na_land():
    return Image.open(ROOT / "assets" / "na_land_40x16.png").convert("L")


def na_xy(lat, lon):
    lo0, lo1, la0, la1 = NA_BOX
    if not (lo0 <= lon < lo1 and la0 <= lat < la1):
        return None
    return int((lon - lo0) / (lo1 - lo0) * 40), int((la1 - lat) / (la1 - la0) * 16)


class MapScreen(Ft8Screen):
    key, short = "ft8_map", "FT8 MAP"
    feed = FT.feed_class("ft8_map", "FT8 station map (NA / world)")
    per = 6

    def pages(self, v):
        return 2

    def draw(self, img, v, k, now):
        located = v.get("located") or []
        if not located and self.quiet(v):
            return idle(img, v, k, now)
        world = page_of(k, 2, self.per) == 1
        latest = v.get("latest") or 0
        home = v.get("home") or ""
        try:
            hl = FT.G.grid_latlon(home)
        except ValueError:
            hl = LF.HOME
        recent = [d for d in located if d["utc"] >= latest - 900]
        if world:
            img.paste(Image.new("RGB", (32, 16), LAND), (0, 0), world_land())
            for d in reversed(recent):
                x, y = _mx(d["loc"]["lat"], d["loc"]["lon"])
                put(img, x, y, snr_col(d["snr"]) if d["utc"] != latest or k % 2 == 0 else WHITE)
            if hl:
                put(img, *_mx(*hl), LPB)
            far = sorted(recent, key=lambda d: -(d["loc"].get("km") or 0))
            txt(img, 34, 0, "DX", AMB)
            rtxt(img, 63, 0, f"{len({d['loc'].get('dxcc') for d in recent if d['loc'].get('dxcc')})}C", DIM)
            if far:
                f = far[0]
                txt(img, 34, 6, fit(f["call"], 30), call_col(f, v.get("mycall") or ""))
                txt(img, 34, 11, fit(f["loc"].get("dist") or f["loc"].get("place") or "", 30), MID)
            return
        img.paste(Image.new("RGB", (40, 16), LAND), (0, 0), na_land())
        inside = [(d, na_xy(d["loc"]["lat"], d["loc"]["lon"])) for d in recent if d["loc"].get("src") == "grid"]
        inside = [(d, p) for d, p in inside if p]
        for d, (x, y) in reversed(inside):
            put(img, x, y, snr_col(d["snr"]) if d["utc"] != latest or k % 2 == 0 else WHITE)
        hp = na_xy(*hl) if hl else None
        if hp:
            put(img, *hp, LPB if k % 2 == 0 else PB)
        txt(img, 41, 0, "NA", AMB)
        rtxt(img, 63, 0, f"{len(inside)}", DIM)
        if inside:
            f = max(inside, key=lambda t: t[0]["loc"].get("km") or 0)[0]
            txt(img, 41, 6, fit(f["call"], 23), call_col(f, v.get("mycall") or ""))
            txt(img, 41, 11, fit((f["loc"].get("dist") or "").replace("MI", "M").replace("KM", "K"), 23), MID)


# ================================================================== 7 top callers
class TopScreen(Ft8Screen):
    key, short = "ft8_top", "FT8 TOP"
    feed = FT.feed_class("ft8_top", "FT8 band activity / top callers")
    ROWS = 2
    MAXC = 8

    def pages(self, v):
        return max(1, math.ceil(min(self.MAXC, len(v.get("calls") or [])) / self.ROWS))

    def draw(self, img, v, k, now):
        calls = (v.get("calls") or [])[: self.MAXC]
        if not calls:
            return idle(img, v, k, now)
        np = self.pages(v)
        p = page_of(k, np, self.per)
        band, _ = band_mode(v)
        x = txt(img, 0, 0, band, AMB)
        txt(img, x + 2, 0, f"{v.get('unique', len(calls))} STNS", CREAM)
        rtxt(img, 63, 0, f"{p + 1}/{np}", DIM)
        for j, c in enumerate(calls[p * self.ROWS:(p + 1) * self.ROWS]):
            y = 6 + 5 * j + j
            i = p * self.ROWS + j
            rk = f"{i + 1}"
            txt(img, 0, y, rk, GREY)
            sn = snr_txt(c["best"])
            n = f"X{c['n']}"
            xr = 63 - text_width(sn) - 2 - text_width(n)
            txt(img, 6, y, fit(c["call"], xr - 8), call_col(c["d"], v.get("mycall") or ""))
            txt(img, xr, y, n, AMB)
            rtxt(img, 63, y, sn, snr_col(c["best"]))


# ================================================================== 8 alerts
ALERT_STYLE = {"MYCALL": ("YOUR CALL!", MAG), "NEWDXCC": ("NEW DXCC", AMB), "FARDX": ("FAR DX", PB),
               "NEWGRID": ("NEW GRID", OG2)}


class AlertScreen(Ft8Screen):
    key, short = "ft8_alert", "FT8 ALERT"
    feed = FT.feed_class("ft8_alert", "FT8 alerts (my call, new DXCC/grid)")
    MAXA = 6

    def pages(self, v):
        return max(1, min(self.MAXA, len(v.get("alerts") or [])))

    def draw(self, img, v, k, now):
        al = (v.get("alerts") or [])[: self.MAXA]
        mc = v.get("mycall") or ""
        if not al:
            band, mode = band_mode(v)
            txt(img, 0, 0, "NO ALERTS", OG2)
            put(img, 63, 0, OG if k % 4 < 2 else DOG)
            txt(img, 0, 6, f"{v.get('grids', 0)} GRIDS {v.get('dxcc', 0)} DXCC", CREAM)
            txt(img, 0, 11, f"WATCH {mc}" if mc else "SET MY CALL", GREY if mc else DIM)
            return
        p = page_of(k, len(al), self.per)
        a = al[p]
        d = a["d"]
        loc = d.get("loc") or {}
        label, col = ALERT_STYLE.get(a["kind"], (a["kind"], AMB))
        if a["kind"] == "MYCALL":
            label = "YOU HEARD" if d.get("call") == mc else "CALLING YOU" if d.get("to") == mc else "YOUR CALL"
        flash = k % 2 == 0
        if a["kind"] == "MYCALL" and flash:
            ImageDraw.Draw(img).rectangle((0, 0, 63, 4), fill=DMAG)
        txt(img, 0, 0, label, col if (flash or a["kind"] != "MYCALL") else MAG)
        rtxt(img, 63, 0, LF.ago(a["at"], now), GREY if a["kind"] != "MYCALL" else CREAM)
        sn = snr_txt(d["snr"])
        x = txt(img, 0, 6, fit(d["call"] or d["msg"], 40), call_col(d, mc))
        if d.get("grid"):
            txt(img, x + 2, 6, d["grid"][:4], OG)
        rtxt(img, 63, 6, sn, snr_col(d["snr"]))
        if a["kind"] == "MYCALL" and d.get("call") != mc:
            txt(img, 0, 11, fit(d["msg"], 64), LPB)
        else:
            dist = f"{loc.get('dist', '')} {loc.get('dir', '')}".strip()
            dw = text_width(dist) + 3 if dist else 0
            txt(img, 0, 11, fit(loc.get("place") or loc.get("country") or "", 64 - dw), LPB)
            if dist:
                rtxt(img, 63, 11, dist, MID)
        if len(al) > 1:
            for q in range(len(al)):
                put(img, 63 - 2 * (len(al) - 1 - q), 5, col if q == p else DIM)


SCREENS_FT8 = [TickerScreen(), CqScreen(), SnrScreen(), AudioScreen(), CycleScreen(), MapScreen(), TopScreen(),
               AlertScreen()]
from . import livedata_screens as _SC  # noqa: E402  (works whichever module is imported first)
_SC.SCREENS.update({s.key: s for s in SCREENS_FT8})
