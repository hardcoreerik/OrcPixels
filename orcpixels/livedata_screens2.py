"""Live Data screens 16-30: markets + news (feeds in orcpixels/sources/marketnews.py),
plus the optional X.com screen (needs a token; shows SET TOKEN otherwise).

Readable at ~1 fps on the legacy transport:
  * the stock ticker is a unifont marquee that jumps STEP px per frame; its strip is
    padded to a multiple of STEP and drawn tiled, so frame loop-1 -> 0 is one ordinary
    step (seamless, no jump);
  * quotes / headlines are PAGED (2 lines of 3x5 text per page, `per` frames each) with
    the page boundary offset by half a page like the first 15 screens.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw

from .livedata_screens import (AMB, CREAM, DAMB, DIM, GREY, H, LPB, OG, OG2, PB, RED, W, WHITE,
                               Screen, _uni, big, big_w, dimc, fit, put, rtxt, txt, wrap)
from .sources import livefeeds as LF
from .sources import marketnews as MN
from .tinyfont import text_width

UP, DOWN, FLAT = (60, 225, 90), (255, 70, 55), (150, 156, 170)
STEP = 16                 # marquee px per frame (2 unifont chars per ~1 s frame)


def chg_col(x):
    return FLAT if x is None or abs(x) < 0.005 else (UP if x > 0 else DOWN)


def fmt_price(p: float, yield_: bool = False) -> str:
    if yield_:
        return f"{p:.2f}%"
    a = abs(p)
    if a >= 10000:
        return f"{p:.0f}"
    if a >= 1000:
        return f"{p:.1f}"
    if a >= 1:
        return f"{p:.2f}"
    return f"{p:.4f}"


def fmt_pct(x) -> str:
    return "--" if x is None else f"{x:+.2f}%" if abs(x) < 10 else f"{x:+.1f}%"


def fmt_chg(q) -> str:
    c = q.get("chg")
    if c is None:
        return ""
    if q.get("yield"):
        return f"{c * 100:+.0f}BP"
    a = abs(c)
    return f"{c:+.0f}" if a >= 100 else (f"{c:+.1f}" if a >= 10 else f"{c:+.2f}")


# ------------------------------------------------------------------ marquee
@lru_cache(maxsize=32)
def strip(segs: tuple, step: int = STEP, y: int = -1) -> Image.Image:
    """One period of a marquee: segments (text, rgb) side by side, padded to a multiple
    of step and at least W wide (so tiling two copies always covers the panel)."""
    width = sum(big_w(t) for t, _ in segs)
    L = max(width, W)
    L = int(math.ceil(L / step) * step)
    im = Image.new("RGB", (L, H))
    x = 0
    for t, c in segs:
        big(im, x, y, t, c)
        x += big_w(t)
    return im


def marquee(img, segs: tuple, k: int, step: int = STEP):
    s = strip(segs, step)
    o = (k * step) % s.width
    x = -o
    while x < W:
        img.paste(s, (x, 0))
        x += s.width


def marquee_len(segs: tuple, step: int = STEP) -> int:
    return strip(segs, step).width // step


def sparkline(img, x0, y0, w, h, series, prev=None, col=None, k=0):
    pts = [p for p in series if p is not None]
    if len(pts) < 2:
        return
    n = w
    vals = [pts[int(i * (len(pts) - 1) / (n - 1))] for i in range(n)]
    lo, hi = min(vals + ([prev] if prev else [])), max(vals + ([prev] if prev else []))
    span = (hi - lo) or 1.0
    yv = lambda v: y0 + h - 1 - int(round((v - lo) / span * (h - 1)))
    col = col or chg_col(vals[-1] - (prev if prev else vals[0]))
    if prev:
        for x in range(0, n, 2):
            put(img, x0 + x, yv(prev), DIM)
    d = ImageDraw.Draw(img)
    last = None
    for i, v in enumerate(vals):
        p = (x0 + i, yv(v))
        if last:
            d.line((last, p), fill=dimc(col, 0.85))
        last = p
    put(img, *last, CREAM if k % 2 == 0 else col)


class MarketScreen(Screen):
    group = "Markets"


# ------------------------------------------------------------------ screens
def state_seg(now):
    """Fixed-width (10 chars) so the marquee period never depends on the clock."""
    st = MN.market_state(now)["state"] if now else "closed"
    t, c = {"open": ("NYSE OPEN", OG), "pre": ("PRE-MARKET", AMB), "after": ("AFTER HRS", AMB)}.get(
        st, ("MKT CLOSED", GREY))
    return t.ljust(10), c


class StocksScreen(MarketScreen):
    key, short, feed = "stocks", "STOCKS", MN.Stocks

    def segs(self, v, now) -> tuple:
        out = [(state_seg(now)[0], state_seg(now)[1]), ("   ", DIM)]
        for q in v["quotes"]:
            out += [(q["name"], AMB), (" " + fmt_price(q["price"]), CREAM),
                    (" " + fmt_pct(q.get("pct")), chg_col(q.get("pct"))), ("   ", DIM)]
        return tuple(out)

    def loop(self, v):
        return marquee_len(self.segs(v, None)) if v and v.get("quotes") else 1

    def draw(self, img, v, k, now):
        marquee(img, self.segs(v, now), k)


class QuoteScreen(MarketScreen):
    """One symbol per page: name + % change, 1-day sparkline, price + change."""
    per = 4

    def pages(self, v):
        return max(1, len(v.get("quotes") or []))

    def draw(self, img, v, k, now):
        qs = v["quotes"]
        if not qs:
            txt(img, 0, 0, self.short, LPB)
            txt(img, 0, 9, "NO QUOTES", AMB)
            return
        q = qs[LF_page(k, len(qs), self.per)]
        pc = chg_col(q.get("pct"))
        txt(img, 0, 0, fit(q["name"], 36), AMB)
        rtxt(img, 63, 0, fmt_pct(q.get("pct")) if not q.get("yield") else fmt_chg(q), pc)
        sparkline(img, 0, 6, 30, 10, q.get("series") or [], q.get("prev"), pc, k)
        rtxt(img, 63, 6, fit(fmt_price(q["price"], q.get("yield")), 31), CREAM)
        sub = fmt_chg(q) if not q.get("yield") else (f"PRV {q['prev']:.2f}" if q.get("prev") else "")
        rtxt(img, 63, 11, fit(sub, 31), dimc(pc, 0.8))
        if len(qs) > 1:                                   # page dots
            for i in range(len(qs)):
                put(img, 31 + 2 * i, 15, LPB if i == LF_page(k, len(qs), self.per) else DIM)


def LF_page(k, pages, per):
    from .livedata_screens import page_of
    return page_of(k, pages, per)


class IndexesScreen(QuoteScreen):
    key, short, feed = "indexes", "INDEXES", MN.Indexes


class CommodScreen(QuoteScreen):
    key, short, feed = "commod", "GOLD OIL", MN.Commodities


class RatesScreen(QuoteScreen):
    key, short, feed = "rates", "RATES", MN.Rates


class SparkScreen(MarketScreen):
    key, short, feed = "spark", "SPARK", MN.Spark

    def loop(self, v):
        return 2

    def draw(self, img, v, k, now):
        q = v["quotes"][0]
        pc = chg_col(q.get("pct"))
        x = txt(img, 0, 0, fit(q["name"], 24), AMB)
        txt(img, x + 2, 0, fit(fmt_price(q["price"], q.get("yield")), 62 - x - text_width(fmt_pct(q.get("pct"))) - 3), CREAM)
        rtxt(img, 63, 0, fmt_pct(q.get("pct")), pc)
        sparkline(img, 0, 6, 64, 10, q.get("series") or [], q.get("prev"), pc, k)


class CryptoScreen(MarketScreen):
    key, short, feed = "crypto", "CRYPTO", MN.Crypto

    def pages(self, v):
        return max(1, len(v.get("coins") or []))

    def draw(self, img, v, k, now):
        cs = v["coins"]
        c = cs[LF_page(k, len(cs), self.per)]
        p = c["price"]
        s = f"{p:.0f}" if p >= 1000 else (f"{p:.1f}" if p >= 100 else (f"{p:.2f}" if p >= 1 else f"{p:.3f}"))
        x = big(img, 0, -1, s, CREAM)
        xr = max(x + 2, 43)
        txt(img, xr, 0, fit(c["sym"], 63 - xr), AMB)
        if k % 2 == 0:
            put(img, 63, 2, AMB)
        txt(img, xr, 6, fit(fmt_pct(c.get("pct")), 64 - xr), chg_col(c.get("pct")))
        txt(img, xr, 11, fit("24H" if c.get("pct") is not None else "SPOT", 64 - xr), DIM)
        pc = chg_col(c.get("pct"))
        ImageDraw.Draw(img).line((0, 15, max(0, x - 2), 15), fill=dimc(pc, 0.45))


class ForexScreen(MarketScreen):
    key, short, feed = "forex", "FOREX", MN.Forex

    def pages(self, v):
        return max(1, (len(v.get("pairs") or []) + 1) // 2)

    def draw(self, img, v, k, now):
        prs = v["pairs"]
        n = self.pages(v)
        p = LF_page(k, n, self.per)
        txt(img, 0, 0, "FOREX", AMB)
        date = (v.get("date") or "")[5:].replace("-", "/")
        rtxt(img, 63, 0, f"ECB {date}" if date else "ECB", GREY)
        for i, pr in enumerate(prs[2 * p: 2 * p + 2]):
            r = pr["rate"]
            rs = f"{r:.2f}" if r >= 100 else (f"{r:.3f}" if r >= 10 else f"{r:.4f}")
            txt(img, 0, 6 + 5 * i, pr["pair"], LPB)
            rtxt(img, 63, 6 + 5 * i, rs, CREAM)


def countdown(s: float) -> str:
    s = max(0, int(s))
    if s >= 86400:
        return f"{s // 86400}D{(s % 86400) // 3600:02d}"
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


class MarketClockScreen(MarketScreen):
    key, short, feed = "market_clock", "NYSE", MN.MarketClock

    def loop(self, v):
        return 4

    def draw(self, img, v, k, now):
        st = MN.market_state(now)
        open_ = st["state"] == "open"
        target = st["close"] if open_ else st["open"]
        cd = countdown((target or now) - now)
        col = OG2 if open_ else CREAM
        if ":" in cd and k % 2 == 1:                       # blinking colon
            a, b = cd.split(":")
            x = big(img, 0, -1, a, col)
            x = big(img, x + big_w(":"), -1, b, col)
        else:
            x = big(img, 0, -1, cd, col)
        xr = max(x + 2, 41)
        txt(img, xr, 0, "NYSE", AMB)
        name, scol = {"open": ("OPEN", OG), "pre": ("PRE-MKT", AMB), "after": ("AFTER", AMB)}.get(
            st["state"], ("CLOSED", RED))
        txt(img, xr, 6, fit(name, 64 - xr), scol)
        if target:                                         # when it opens / closes: day, then PT time
            same_day = LF.pt(target).date() == LF.pt(now).date()
            day = "TODAY" if same_day else LF.pt(target).strftime("%a").upper()
            line = day if (k + 1) // 2 % 2 == 0 else LF.hm12(target).replace(":00", "")
            txt(img, xr, 11, fit(line, 64 - xr), LPB)
        if open_:
            frac = (now - st["open"]) / max(1.0, st["close"] - st["open"])
            ImageDraw.Draw(img).line((0, 15, int(frac * (xr - 3)), 15), fill=dimc(OG, 0.6))
        elif st.get("holiday"):
            ImageDraw.Draw(img).line((0, 15, xr - 3, 15), fill=DAMB)


# ------------------------------------------------------------------ news
TAGC = {"NPR": (40, 120, 255), "BBC": (230, 60, 50), "OPB": OG, "OREGONLIVE": (90, 170, 255), "ARS": (255, 120, 0),
        "VERGE": (225, 40, 160), "ARRL": LPB, "R/HAM": (255, 69, 0), "HN": (255, 102, 0)}


class NewsScreen(Screen):
    group = "News"
    per = 4
    MAX_ITEMS = 6
    LINES = 2

    @staticmethod
    @lru_cache(maxsize=256)
    def _lines(title: str) -> tuple:
        return tuple(wrap(title, 64, 40)) or ("",)

    def entries(self, v) -> list[tuple[int, int, int]]:
        out = []
        for i, it in enumerate((v.get("items") or [])[: self.MAX_ITEMS]):
            ln = self._lines(it["title"])
            n = max(1, math.ceil(len(ln) / self.LINES))
            out += [(i, p, n) for p in range(n)]
        return out

    def loop(self, v):
        return self.per * max(1, len(self.entries(v))) if v else self.per

    def head(self, v, it) -> str:
        return LF.ago(it.get("at")) if it.get("at") else ""

    def draw(self, img, v, k, now):
        ents = self.entries(v)
        items = v.get("items") or []
        if not ents:
            txt(img, 0, 0, v.get("tag", self.short), AMB)
            txt(img, 0, 9, "NO HEADLINES", GREY)
            return
        i, p, n = ents[((k + self.per // 2) // self.per) % len(ents)]
        it = items[i]
        tag = v.get("tag") or self.short
        cnt = f"{i + 1}/{min(len(items), self.MAX_ITEMS)}"
        tc = TAGC.get(tag, AMB)
        x = txt(img, 0, 0, fit(tag, 40 - text_width(cnt)), tc)
        h = self.head(v, it)
        if h:
            txt(img, x + 2, 0, fit(h, 63 - text_width(cnt) - x - 4), GREY)
        rtxt(img, 63, 0, cnt, DIM)
        ln = self._lines(it["title"])[p * self.LINES:(p + 1) * self.LINES]
        for j, line in enumerate(ln):
            txt(img, 0, 6 + 5 * j, line, CREAM if j == 0 or p else WHITE)
        if n > 1:                                          # "more" marks for multi-page headlines
            for q in range(n):
                put(img, 63 - 2 * (n - 1 - q), 5, OG if q == p else DIM)


class NprScreen(NewsScreen):
    key, short, feed = "news_npr", "NPR", MN.NprNews


class WorldScreen(NewsScreen):
    key, short, feed = "news_world", "BBC", MN.WorldNews


class OregonScreen(NewsScreen):
    key, short, feed = "news_oregon", "OREGON", MN.OregonNews


class TechScreen(NewsScreen):
    key, short, feed = "news_tech", "TECH", MN.TechNews


class ArrlScreen(NewsScreen):
    key, short, feed = "news_arrl", "ARRL", MN.ArrlNews


class RedditScreen(NewsScreen):
    key, short, feed = "reddit_ham", "R/HAM", MN.RedditHam


class HnScreen(NewsScreen):
    key, short, feed = "hn", "HN", MN.HackerNews

    def head(self, v, it):
        return f"{int(it['score'])}P" if it.get("score") is not None else super().head(v, it)


class XScreen(NewsScreen):
    key, short, feed = "x_feed", "X.COM", MN.XFeed
    group = "Optional"


SCREENS2 = [StocksScreen(), IndexesScreen(), CryptoScreen(), CommodScreen(), ForexScreen(), MarketClockScreen(),
            SparkScreen(), RatesScreen(), NprScreen(), WorldScreen(), HnScreen(), OregonScreen(), TechScreen(),
            ArrlScreen(), RedditScreen()]
OPTIONAL2 = [XScreen()]
_ = (PB, _uni)
