"""Live-source categories: a small registry, so adding the 100th category or screen
costs one line or one plugin module (orcpixels/live_plugins/<name>.py).

    from orcpixels import categories as CAT
    CAT.category("sports", "Sports", order=70, color=(255, 120, 40), blurb="ESPN scores")
    CAT.assign("my_screen", "sports")        # or set `category = "sports"` on the Screen

Screens live in livedata_screens.SCREENS (key -> Screen); a screen's category is its
`category` attribute, else ASSIGN[key], else "other". Features that are not data screens
(OrcSDR Live, the AI buddy) are listed as category *links* so the UI shows them in place.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Category:
    id: str
    title: str
    order: int = 500
    color: tuple = (150, 156, 170)
    blurb: str = ""
    links: list = field(default_factory=list)     # [{"title", "href"}]: non-screen features


CATEGORIES: dict[str, Category] = {}
ASSIGN: dict[str, str] = {}


def category(cid: str, title: str, order: int = 500, color=(150, 156, 170), blurb: str = "", links=None) -> Category:
    c = CATEGORIES.get(cid)
    if c is None:
        c = CATEGORIES[cid] = Category(cid, title, order, tuple(color), blurb, list(links or []))
    return c


def assign(key: str, cid: str) -> None:
    ASSIGN[key] = cid


def of(key: str, screen=None) -> str:
    cid = getattr(screen, "category", None) or ASSIGN.get(key) or "other"
    return cid if cid in CATEGORIES else "other"


def ordered() -> list[Category]:
    return sorted(CATEGORIES.values(), key=lambda c: (c.order, c.title))


def grouped(screens: dict) -> list[tuple[Category, list[str]]]:
    by: dict[str, list[str]] = {}
    for k, s in screens.items():
        by.setdefault(of(k, s), []).append(k)
    return [(c, by.get(c.id, [])) for c in ordered() if by.get(c.id) or c.links]


def as_json(screens: dict) -> list[dict]:
    return [{"id": c.id, "title": c.title, "order": c.order, "color": "#%02x%02x%02x" % c.color,
             "blurb": c.blurb, "links": c.links, "screens": keys} for c, keys in grouped(screens)]


# ------------------------------------------------------------------ the built-in set
category("apps", "Apps", 10, (40, 210, 90), "OrcSDR, AI status, GitHub",
         links=[{"title": "OrcSDR Live", "card": "card-orcsdr"}, {"title": "AI status (Codex / Claude / Grok / ChatGPT)", "card": "card-ai"}])
category("weather", "Weather", 20, (120, 200, 255), "NWS conditions, forecast, alerts, air quality")
category("markets", "Markets & Money", 30, (60, 225, 90), "stocks, indexes, crypto, forex, rates")
category("news", "News", 40, (255, 236, 190), "headlines from public RSS feeds")
category("space", "Space & Science", 50, (180, 120, 255), "Kp, X-ray flares, ISS, earthquakes")
category("ham", "Ham Radio", 60, (255, 168, 20), "FT8 / FT4 and HF band conditions (DX tools to come)",
         links=[{"title": "OrcSDR connection (FT8 port / URL)", "card": "card-sdrconn"}])
category("sports", "Sports", 70, (255, 120, 40), "ESPN scoreboards: live scores, today's games, favourite teams")
category("entertainment", "Entertainment", 80, (255, 90, 170), "TV schedule, airing now, charts, trending")
category("local", "Time & Local", 90, (90, 170, 255), "clocks, sun and moon, tides, rivers, aircraft")
category("other", "Other", 999)

for _k in ("github",):
    assign(_k, "apps")
for _k in ("wx_now", "forecast", "alerts", "aqi"):
    assign(_k, "weather")
for _k in ("stocks", "indexes", "crypto", "commod", "forex", "market_clock", "spark", "rates"):
    assign(_k, "markets")
for _k in ("news_npr", "news_world", "hn", "news_oregon", "news_tech", "news_arrl", "reddit_ham", "x_feed"):
    assign(_k, "news")
for _k in ("spacewx", "xray", "iss", "quake"):
    assign(_k, "space")
for _k in ("hf_bands", "ft8_ticker", "ft8_cq", "ft8_snr", "ft8_audio", "ft8_cycle", "ft8_map", "ft8_top", "ft8_alert"):
    assign(_k, "ham")
for _k in ("clock", "sunmoon", "tides", "river", "aircraft"):
    assign(_k, "local")
