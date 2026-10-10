"""Markets + news feeds for Live Data screens 16-30 (orcpixels/livedata_screens2.py).

Same Feed contract as livefeeds.py (due-based polling, backoff, stale / max_stale, the
monkeypatchable livefeeds.HTTP). All sources are free and keyless:
  * Yahoo Finance chart API (v7 spark: many symbols in ONE request; v8 chart per symbol
    as the fallback). Stooq's free CSV is gone (404 / API key since 2025), so Yahoo's two
    endpoints back each other up.
  * CoinGecko simple price (24 h change), Coinbase spot price as the fallback.
  * Frankfurter (ECB reference rates; .dev, .app fallback).
  * RSS / Atom: NPR, BBC, OPB (OregonLive fallback), Ars Technica (The Verge fallback),
    ARRL, Reddit r/amateurradio (.rss: the JSON API answers 403), Hacker News (Firebase
    API, hnrss.org fallback).
  * X.com has no free read API: XFeed is OPTIONAL and never touches the network unless a
    bearer token is set (env X_BEARER_TOKEN or settings livedata_x_bearer_token, which
    lives in the gitignored config/settings.json).
Symbols, crypto, currencies and every RSS URL are configurable (settings keys in CONFIG,
edited in the Live tab: POST /api/livedata/feed-config).
Market feeds poll every 2-5 min while US markets are open (incl. pre/after hours) and
every 30 min when closed; news every 10-60 min.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import os
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

from . import livefeeds as LF
from .livefeeds import Feed, _num

# ------------------------------------------------------------------ configuration
CONFIG = {        # settings key -> (default, kind, label)
    "livedata_stock_symbols": ("AAPL,MSFT,NVDA,GOOGL,AMZN,TSLA", "symbols", "Stock ticker symbols"),
    "livedata_index_symbols": ("^GSPC,^DJI,^IXIC", "symbols", "Index symbols"),
    "livedata_commodity_symbols": ("GC=F,CL=F,SI=F", "symbols", "Commodity futures"),
    "livedata_rates_symbols": ("^TNX,^TYX,^VIX", "symbols", "Rates / volatility"),
    "livedata_spark_symbol": ("SPY", "symbol", "Sparkline symbol"),
    "livedata_crypto": ("BTC,ETH", "symbols", "Crypto (tickers)"),
    "livedata_forex": ("EUR,GBP,JPY,CAD,MXN", "currencies", "Currencies vs USD"),
    "livedata_rss_npr": ("https://feeds.npr.org/1001/rss.xml", "url", "NPR RSS"),
    "livedata_rss_world": ("https://feeds.bbci.co.uk/news/world/rss.xml", "url", "World news RSS"),
    "livedata_rss_oregon": ("https://www.opb.org/arc/outboundfeeds/rss/?outputType=xml", "url", "Oregon news RSS"),
    "livedata_rss_tech": ("https://feeds.arstechnica.com/arstechnica/index", "url", "Tech news RSS"),
    "livedata_rss_arrl": ("https://www.arrl.org/news/rss", "url", "ARRL news RSS"),
    "livedata_rss_reddit": ("https://www.reddit.com/r/amateurradio/top/.rss?t=day", "url", "Reddit RSS"),
    "livedata_x_user": ("", "handle", "X account (optional, needs token)"),
}
SECRET_KEYS = ("livedata_x_bearer_token",)          # write-only via the API, never echoed
SYM_RE = re.compile(r"^[A-Z0-9^=.\-]{1,12}$")
CUR_RE = re.compile(r"^[A-Z]{3}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
MAX_SYMBOLS = 12


def cfg(key: str) -> str:
    v = LF._setting(key)
    return str(v).strip() if v else CONFIG[key][0]


def cfg_list(key: str) -> list[str]:
    out = [s.strip().upper() for s in cfg(key).split(",") if s.strip()]
    return list(dict.fromkeys(out))[:MAX_SYMBOLS] or [s for s in CONFIG[key][0].split(",")]


def validate(key: str, value) -> str:
    """Normalised settings value, or ValueError. Empty string = back to the default."""
    if key not in CONFIG and key not in SECRET_KEYS:
        raise ValueError(f"unknown setting {key}")
    v = ("" if value is None else str(value)).strip()
    if not v or key in SECRET_KEYS:
        return v
    kind = CONFIG[key][1]
    if kind in ("symbols", "symbol", "currencies"):
        items = [s.strip().upper() for s in v.split(",") if s.strip()]
        rx = CUR_RE if kind == "currencies" else SYM_RE
        bad = [s for s in items if not rx.match(s)]
        if bad or not items:
            raise ValueError(f"{key}: bad symbol(s) {', '.join(bad) or v}")
        if kind == "symbol" and len(items) != 1:
            raise ValueError(f"{key}: one symbol only")
        if len(items) > MAX_SYMBOLS:
            raise ValueError(f"{key}: at most {MAX_SYMBOLS}")
        return ",".join(dict.fromkeys(items))
    if kind == "url":
        u = urllib.parse.urlparse(v)
        if u.scheme not in ("http", "https") or not u.netloc or len(v) > 400:
            raise ValueError(f"{key}: must be an http(s) URL")
        return v
    if kind == "handle":
        v = v.lstrip("@")
        if not HANDLE_RE.match(v):
            raise ValueError(f"{key}: bad X handle")
        return v
    return v


# ------------------------------------------------------------------ text helpers
_FOLD = {"\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201c": "'", "\u201d": "'", "\u2032": "'",
         "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u2026": "...", "\u00a0": " ",
         '"': "'", "\u00d7": "X", "\u20ac": "EUR", "\u00a3": "GBP", "$": "", "@": "AT ", ";": ",", "|": "-",
         "[": "(", "]": ")", "{": "(", "}": ")", "~": "-"}


def clean(s: str) -> str:
    """Headline -> plain uppercase ASCII the 3x5 font can draw (entities, smart quotes,
    accents folded; tags and URLs dropped; whitespace collapsed)."""
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"https?://\S+", "", s)
    s = "".join(_FOLD.get(c, c) for c in s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    from ..tinyfont import _G
    s = "".join(c if c.upper() in _G else " " for c in s)
    return re.sub(r"\s+", " ", s).strip().upper()


def _date(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip()
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return LF.parse_iso(s)
    except ValueError:
        return None


ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(data: bytes, limit: int = 8) -> list[dict]:
    """RSS 2.0 or Atom -> [{"title", "at"}] newest first (OPB's feed is not sorted)."""
    root = ET.fromstring(data)
    out = []
    for it in root.iter("item"):
        out.append({"title": clean(it.findtext("title") or ""), "at": _date(it.findtext("pubDate") or
                    it.findtext("{http://purl.org/dc/elements/1.1/}date"))})
    if not out:
        for e in root.iter(ATOM + "entry"):
            out.append({"title": clean(e.findtext(ATOM + "title") or ""),
                        "at": _date(e.findtext(ATOM + "published") or e.findtext(ATOM + "updated"))})
    out = [o for o in out if o["title"]]
    out.sort(key=lambda o: -(o["at"] or 0))
    seen, uniq = set(), []
    for o in out:
        if o["title"] not in seen:
            seen.add(o["title"])
            uniq.append(o)
    return uniq[:limit]


# ------------------------------------------------------------------ US market hours
def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    return dt.date(y, mo, (h + l - 7 * m + 114) % 31 + 1)


def _nth(y, mo, wd, n):
    d = dt.date(y, mo, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def _last(y, mo, wd):
    d = dt.date(y, mo + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else (d + dt.timedelta(days=1) if d.weekday() == 6 else d)


def nyse_holidays(y: int) -> dict[dt.date, str]:
    h = {}
    ny = dt.date(y, 1, 1)
    if ny.weekday() != 5:                       # NYSE skips a Saturday New Year
        h[_observed(ny)] = "NEW YEAR"
    h[_nth(y, 1, 0, 3)] = "MLK DAY"
    h[_nth(y, 2, 0, 3)] = "PRESIDENTS"
    h[_easter(y) - dt.timedelta(days=2)] = "GOOD FRI"
    h[_last(y, 5, 0)] = "MEMORIAL"
    if y >= 2022:
        h[_observed(dt.date(y, 6, 19))] = "JUNETEENTH"
    h[_observed(dt.date(y, 7, 4))] = "JULY 4"
    h[_nth(y, 9, 0, 1)] = "LABOR DAY"
    h[_nth(y, 11, 3, 4)] = "THANKSGIV"
    h[_observed(dt.date(y, 12, 25))] = "CHRISTMAS"
    return h


def nyse_early_close(d: dt.date) -> bool:
    """1 pm ET closes: July 3, day after Thanksgiving, Christmas Eve (on trading days)."""
    return d in (dt.date(d.year, 7, 3), _nth(d.year, 11, 3, 4) + dt.timedelta(days=1), dt.date(d.year, 12, 24))


def _pt_ts(d: dt.date, hh: int, mm: int) -> float:
    """Epoch of a Pacific wall-clock time (ET is always PT + 3 h: same DST rules)."""
    naive = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=dt.timezone.utc).timestamp()
    off = LF.pt_offset_h(naive + 8 * 3600)
    return naive - off * 3600


def trading_day(d: dt.date) -> tuple[bool, str | None]:
    if d.weekday() >= 5:
        return False, "WEEKEND"
    name = nyse_holidays(d.year).get(d)
    return (False, name) if name else (True, None)


def market_state(ts: float) -> dict:
    """NYSE session at ts: state open | pre | after | closed, the next open/close (epoch),
    session start/end, holiday name if today is one."""
    today = LF.pt(ts).date()
    sess = []
    for i in range(-1, 12):
        d = today + dt.timedelta(days=i)
        ok, _ = trading_day(d)
        if ok:
            close = (10, 0) if nyse_early_close(d) else (13, 0)
            sess.append((d, _pt_ts(d, 6, 30), _pt_ts(d, *close), _pt_ts(d, 1, 0),
                         _pt_ts(d, 17, 0) if close == (13, 0) else _pt_ts(d, 14, 0)))
    _, hol = trading_day(today)
    for d, o, c, pre, post in sess:
        if o <= ts < c:
            return {"state": "open", "open": o, "close": c, "next": c, "holiday": None, "early": nyse_early_close(d)}
    nxt = next((s for s in sess if s[1] > ts), None)
    cur_pre = next((s for s in sess if s[3] <= ts < s[1]), None)
    cur_post = next((s for s in sess if s[2] <= ts < s[4]), None)
    state = "pre" if cur_pre else ("after" if cur_post else "closed")
    return {"state": state, "open": nxt[1] if nxt else None, "close": nxt[2] if nxt else None,
            "next": nxt[1] if nxt else None, "holiday": hol if hol != "WEEKEND" else None,
            "weekend": hol == "WEEKEND", "early": bool(nxt and nyse_early_close(nxt[0]))}


def us_market_active(ts: float | None = None) -> bool:
    return market_state(ts or time.time())["state"] in ("open", "pre", "after")


# ------------------------------------------------------------------ Yahoo quotes
NAMES = {"^GSPC": "S&P 500", "^DJI": "DOW", "^IXIC": "NASDAQ", "^RUT": "RUSSELL", "^NDX": "NDX 100",
         "GC=F": "GOLD", "CL=F": "OIL WTI", "BZ=F": "BRENT", "SI=F": "SILVER", "NG=F": "NAT GAS",
         "HG=F": "COPPER", "^TNX": "10Y", "^TYX": "30Y", "^FVX": "5Y", "^IRX": "3M", "^VIX": "VIX",
         "BTC-USD": "BTC", "ETH-USD": "ETH"}
YIELDS = {"^TNX", "^TYX", "^FVX", "^IRX"}


def sym_name(s: str) -> str:
    return NAMES.get(s, s.replace("=F", "").replace("^", "").replace("-USD", ""))


def _quote(sym: str, meta: dict, closes: list) -> dict:
    p = _num(meta.get("regularMarketPrice"))
    prev = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
    series = [c for c in closes if _num(c) is not None]
    if p is None and series:
        p = series[-1]
    if p is None:
        raise ValueError(f"no price for {sym}")
    chg = None if not prev else p - prev
    return {"sym": sym, "name": sym_name(sym), "price": float(p), "prev": prev, "chg": chg,
            "pct": None if not prev else chg / prev * 100, "series": series[-80:],
            "at": _num(meta.get("regularMarketTime")), "yield": sym in YIELDS}


class YahooQuotes(Feed):
    """Quotes + 1-day series for a symbol list: ONE v7 spark request for all symbols;
    if that fails, v8 chart per symbol (other host)."""
    source = "Yahoo Finance chart API (v7 spark, v8 chart fallback)"
    setting = "livedata_stock_symbols"
    interval = "15m"
    open_poll_s, closed_poll_s = 120.0, 1800.0

    @property
    def poll_s(self):  # type: ignore[override]
        return self.open_poll_s if us_market_active() else self.closed_poll_s

    def symbols(self) -> list[str]:
        return cfg_list(self.setting)

    def spark_url(self, syms) -> str:
        q = urllib.parse.quote(",".join(syms), safe=",")
        return f"https://query1.finance.yahoo.com/v7/finance/spark?symbols={q}&range=1d&interval={self.interval}"

    @property
    def url(self) -> str:  # type: ignore[override]
        return self.spark_url(self.symbols())

    def chart_url(self, sym: str) -> str:
        return (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}"
                f"?range=1d&interval={self.interval}")

    def _spark(self, syms):
        res = {}
        for r in (self.get_json(self.spark_url(syms)).get("spark") or {}).get("result") or []:
            try:
                resp = r["response"][0]
                closes = ((resp.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                res[r["symbol"]] = _quote(r["symbol"], resp.get("meta") or {}, closes)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return res

    def _chart(self, sym):
        r = self.get_json(self.chart_url(sym))["chart"]["result"][0]
        closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        return _quote(sym, r.get("meta") or {}, closes)

    def fetch(self):
        syms = self.symbols()
        via = "spark"
        try:
            res = self._spark(syms)
            if not res:
                raise ValueError("spark returned no quotes")
        except Exception as e:  # noqa: BLE001
            LF.log.info("%s: Yahoo spark failed (%s); trying v8 chart per symbol", self.key, e)
            res, via, errs = {}, "chart", []
            for s in syms:
                try:
                    res[s] = self._chart(s)
                except Exception as e2:  # noqa: BLE001
                    errs.append(f"{s}: {type(e2).__name__}")
            if not res:
                raise ValueError("no quotes (" + "; ".join(errs)[:100] + ")")
        quotes = [res[s] for s in syms if s in res]
        return {"quotes": quotes, "via": via, "missing": [s for s in syms if s not in res],
                "market": market_state(time.time())["state"]}


class Stocks(YahooQuotes):
    key, title = "stocks", "Stock ticker"
    setting = "livedata_stock_symbols"


class Indexes(YahooQuotes):
    key, title = "indexes", "S&P 500, Dow, Nasdaq"
    setting = "livedata_index_symbols"


class Commodities(YahooQuotes):
    key, title = "commod", "Gold, oil, silver"
    setting = "livedata_commodity_symbols"
    open_poll_s, closed_poll_s = 300.0, 1800.0


class Rates(YahooQuotes):
    key, title = "rates", "Treasury yields + VIX"
    setting = "livedata_rates_symbols"
    open_poll_s, closed_poll_s = 300.0, 1800.0


class Spark(YahooQuotes):
    key, title = "spark", "1-day sparkline"
    setting = "livedata_spark_symbol"
    interval = "5m"
    open_poll_s, closed_poll_s = 300.0, 1800.0

    def symbols(self):
        return cfg_list(self.setting)[:1]

    @property
    def url(self) -> str:  # type: ignore[override]
        return self.chart_url(self.symbols()[0])

    def fetch(self):                  # one symbol: v8 chart (5 min bars), v7 spark fallback
        s = self.symbols()[0]
        try:
            q, via = self._chart(s), "chart"
        except Exception as e:  # noqa: BLE001
            LF.log.info("spark: v8 chart failed (%s); trying v7 spark", e)
            res = self._spark([s])
            if s not in res:
                raise
            q, via = res[s], "spark"
        return {"quotes": [q], "via": via, "missing": [], "market": market_state(time.time())["state"]}


# ------------------------------------------------------------------ crypto / forex
CG_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "DOGE": "dogecoin", "XRP": "ripple",
          "ADA": "cardano", "LTC": "litecoin", "BCH": "bitcoin-cash", "DOT": "polkadot", "AVAX": "avalanche-2",
          "LINK": "chainlink", "XLM": "stellar", "USDC": "usd-coin"}


class Crypto(Feed):
    key, title, source = "crypto", "Bitcoin + Ethereum", "CoinGecko simple price (Coinbase spot fallback)"
    setting = "livedata_crypto"
    poll_s = 180.0           # CoinGecko keyless: ~5-30 calls/min shared; 1 call / 3 min

    def coins(self):
        return cfg_list("livedata_crypto")

    @property
    def url(self) -> str:  # type: ignore[override]
        ids = ",".join(CG_IDS.get(c, c.lower()) for c in self.coins())
        return f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"

    def fetch(self):
        coins = self.coins()
        try:
            j = self.get_json(self.url)
            out = []
            for c in coins:
                r = j.get(CG_IDS.get(c, c.lower())) or {}
                if _num(r.get("usd")) is not None:
                    out.append({"sym": c, "price": float(r["usd"]), "pct": _num(r.get("usd_24h_change"))})
            if not out:
                raise ValueError("no prices")
            via = "coingecko"
        except Exception as e:  # noqa: BLE001
            LF.log.info("crypto: CoinGecko failed (%s); trying Coinbase", e)
            out, via = [], "coinbase"
            for c in coins:
                try:
                    d = self.get_json(f"https://api.coinbase.com/v2/prices/{c}-USD/spot")["data"]
                    out.append({"sym": c, "price": float(d["amount"]), "pct": None})
                except Exception:  # noqa: BLE001
                    continue
            if not out:
                raise
        return {"coins": out, "via": via}


INVERT = {"EUR", "GBP", "AUD", "NZD"}       # quoted XXX/USD by convention


class Forex(Feed):
    key, title, source = "forex", "Forex vs USD", "Frankfurter (ECB reference rates)"
    setting = "livedata_forex"
    poll_s = 3600.0          # ECB publishes once a day (~16:00 CET)

    def curs(self):
        return [c for c in cfg_list("livedata_forex") if c != "USD"]

    @property
    def url(self) -> str:  # type: ignore[override]
        return f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={','.join(self.curs())}"

    def fetch(self):
        try:
            j = self.get_json(self.url)
            via = "frankfurter.dev"
        except Exception as e:  # noqa: BLE001
            LF.log.info("forex: frankfurter.dev failed (%s); trying .app", e)
            j = self.get_json(self.url.replace("api.frankfurter.dev/v1", "api.frankfurter.app"))
            via = "frankfurter.app"
        rates = j.get("rates") or {}
        pairs = []
        for c in self.curs():
            r = _num(rates.get(c))
            if not r:
                continue
            pairs.append({"pair": f"{c}/USD", "rate": 1 / r} if c in INVERT else {"pair": f"USD/{c}", "rate": r})
        if not pairs:
            raise ValueError("no rates")
        return {"pairs": pairs, "date": j.get("date"), "via": via}


class MarketClock(Feed):
    key, title, source = "market_clock", "NYSE open / closed", "computed locally (NYSE hours + holidays)"
    url = "local"
    poll_s = 3600.0
    local = True

    def fetch(self):
        return {"ok": True}


# ------------------------------------------------------------------ news
class News(Feed):
    """RSS/Atom headlines: configured URL first, then the built-in fallbacks."""
    source = ""
    setting = ""
    fallbacks: tuple = ()
    tag = "NEWS"
    poll_s = 900.0
    limit = 6

    @property
    def url(self) -> str:  # type: ignore[override]
        return cfg(self.setting)

    def urls(self):
        return list(dict.fromkeys([self.url, *self.fallbacks]))

    def label_for(self, url: str) -> str:
        return self.tag

    def retry_s(self) -> float:
        """Blocked / rate limited (403, 429): wait a full poll interval, not 30 s."""
        if self.error and re.search(r"HTTP Error (403|429)", self.error):
            return self.poll_s
        return super().retry_s()

    def fetch(self):
        errs = []
        for u in self.urls():
            try:
                items = parse_feed(self.get(u, {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}),
                                   self.limit)
                if not items:
                    raise ValueError("feed has no items")
                return {"items": items, "tag": self.label_for(u), "via": urllib.parse.urlparse(u).netloc}
            except Exception as e:  # noqa: BLE001
                errs.append(f"{urllib.parse.urlparse(u).netloc}: {type(e).__name__}: {e}"[:110])
        raise ValueError(" | ".join(errs)[:230])


class NprNews(News):
    key, title, source = "news_npr", "NPR top stories", "NPR RSS (feeds.npr.org)"
    setting, tag, poll_s = "livedata_rss_npr", "NPR", 900.0
    fallbacks = ("https://feeds.npr.org/1002/rss.xml",)


class WorldNews(News):
    key, title, source = "news_world", "BBC world headlines", "BBC News RSS"
    setting, tag, poll_s = "livedata_rss_world", "BBC", 900.0
    fallbacks = ("https://feeds.bbci.co.uk/news/rss.xml",)


class OregonNews(News):
    key, title, source = "news_oregon", "Oregon headlines", "OPB RSS (OregonLive fallback)"
    setting, tag, poll_s = "livedata_rss_oregon", "OPB", 1800.0     # OPB's feed is ~0.5 MB
    fallbacks = ("https://www.oregonlive.com/arc/outboundfeeds/rss/?outputType=xml",)

    def label_for(self, url):
        return "OREGONLIVE" if "oregonlive" in url else ("OPB" if "opb.org" in url else "OREGON")


class TechNews(News):
    key, title, source = "news_tech", "Tech news", "Ars Technica RSS (The Verge fallback)"
    setting, tag, poll_s = "livedata_rss_tech", "ARS", 1800.0
    fallbacks = ("https://www.theverge.com/rss/index.xml",)

    def label_for(self, url):
        return "VERGE" if "theverge" in url else ("ARS" if "arstechnica" in url else "TECH")


class ArrlNews(News):
    key, title, source = "news_arrl", "ARRL ham radio news", "ARRL news RSS"
    setting, tag, poll_s = "livedata_rss_arrl", "ARRL", 3600.0


class RedditHam(News):
    key, title, source = "reddit_ham", "r/amateurradio top", "Reddit RSS (r/amateurradio top/day)"
    setting, tag, poll_s = "livedata_rss_reddit", "R/HAM", 1800.0
    # unauthenticated Reddit rate-limits per IP (429, 403 "Blocked"): ONE url, no second
    # Reddit request as a fallback, and a full poll interval of backoff (News.retry_s)

    def label_for(self, url):
        m = re.search(r"/r/([A-Za-z0-9_]+)", url)
        sub = (m.group(1) if m else "REDDIT").upper()
        return "R/HAM" if sub == "AMATEURRADIO" else ("R/" + sub)[:12]


class HackerNews(Feed):
    key, title, source = "hn", "Hacker News top", "HN Firebase API (hnrss.org fallback)"
    url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    rss_url = "https://hnrss.org/frontpage?count=6"
    poll_s = 600.0
    N = 5

    def fetch(self):
        try:
            ids = self.get_json(self.url)[: self.N]
            items = []
            for i in ids:
                j = self.get_json(f"https://hacker-news.firebaseio.com/v0/item/{int(i)}.json") or {}
                if j.get("title"):
                    items.append({"title": clean(j["title"]), "at": _num(j.get("time")),
                                  "score": _num(j.get("score")), "comments": _num(j.get("descendants"))})
            if not items:
                raise ValueError("no stories")
            via = "firebase"
        except Exception as e:  # noqa: BLE001
            LF.log.info("hn: Firebase API failed (%s); trying hnrss.org", e)
            items = parse_feed(self.get(self.rss_url), self.N)
            via = "hnrss"
            if not items:
                raise
        return {"items": items, "tag": "HN", "via": via}


class XFeed(Feed):
    """OPTIONAL: latest posts of one X account. X has no free read API, so this needs a
    bearer token (env X_BEARER_TOKEN or settings livedata_x_bearer_token); without one it
    never touches the network and the screen says SET TOKEN."""
    key, title, source = "x_feed", "X.com posts (optional, token)", "X API v2 (needs a bearer token)"
    setting = "livedata_x_user"
    key_env = "X_BEARER_TOKEN"
    poll_s = 1800.0          # paid / metered API: 2 requests per 30 min at most

    def __init__(self):
        super().__init__()
        self._uid: tuple[str, str] | None = None

    def token(self) -> str | None:
        return os.environ.get(self.key_env or "") or LF._setting("livedata_x_bearer_token")

    @property
    def needs_key(self) -> bool:
        return not self.token()

    def handle(self) -> str:
        return cfg("livedata_x_user").lstrip("@")

    @property
    def url(self) -> str:  # type: ignore[override]
        return f"https://api.x.com/2/users/by/username/{self.handle()}"

    def fetch(self):
        tok = self.token()
        if not tok:
            raise RuntimeError("NeedsToken: set X_BEARER_TOKEN or livedata_x_bearer_token")
        h = {"Authorization": f"Bearer {tok}"}
        u = self.handle()
        if not self._uid or self._uid[0] != u:
            self._uid = (u, self.get_json(self.url, h)["data"]["id"])
        j = self.get_json(f"https://api.x.com/2/users/{self._uid[1]}/tweets?max_results=5"
                          "&exclude=retweets,replies&tweet.fields=created_at", h)
        items = [{"title": clean(t.get("text", "")), "at": _date(t.get("created_at"))} for t in j.get("data") or []]
        items = [i for i in items if i["title"]]
        if not items:
            raise ValueError("no posts")
        return {"items": items, "tag": "@" + u.upper(), "via": "api.x.com"}


FEEDS2 = [Stocks, Indexes, Crypto, Commodities, Forex, MarketClock, Spark, Rates,
          NprNews, WorldNews, HackerNews, OregonNews, TechNews, ArrlNews, RedditHam]
OPTIONAL = [XFeed]
