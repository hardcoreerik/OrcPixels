"""Games (2026-10-14), keyless:

  FreeToGame (freetogame.com/api, attribution requested): newest free-to-play releases
  CheapShark (cheapshark.com/api: Steam / PC deals; asks for polite use): best-rated Steam deals
"""
from __future__ import annotations

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (90, 230, 230)
CAT.category("games", "Games", 92, COLOR, "free-to-play releases, PC game deals")


class FreeGamesFeed(LF.Feed):
    key, title, source, poll_s = "free_games", "New free-to-play games", "FreeToGame API (keyless)", 12 * 3600.0
    url = "https://www.freetogame.com/api/games?sort-by=release-date"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 6 * 3600.0)
        rows = [{"t": A.clean(g.get("title")).upper(), "genre": A.clean(g.get("genre")).upper(),
                 "plat": "WEB" if "Web" in (g.get("platform") or "") else "PC", "date": g.get("release_date") or ""}
                for g in (d or [])[:12]]
        if not rows:
            raise ValueError("no games")
        return {"rows": rows}


class FreeGames(A.ListScreen):
    key, short, feed, category, color, tag = "free_games", "FREE GAMES", FreeGamesFeed, "games", COLOR, "F2P"

    def items(self, v):
        return [(r["t"], f"{r['genre']} {r['plat']}", r["date"][5:]) for r in (v or {}).get("rows", [])]


class DealsFeed(LF.Feed):
    key, title, source, poll_s = "game_deals", "Steam deals under $15", "CheapShark deals API (keyless)", 3 * 3600.0
    url = "https://www.cheapshark.com/api/1.0/deals?storeID=1&upperPrice=15&pageSize=12&sortBy=Deal%20Rating"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 2 * 3600.0)
        rows = [{"t": A.clean(g.get("title")).upper(), "sale": float(g.get("salePrice") or 0),
                 "norm": float(g.get("normalPrice") or 0), "off": round(float(g.get("savings") or 0)),
                 "rating": A.clean(g.get("steamRatingText")).upper()} for g in d or []]
        if not rows:
            raise ValueError("no deals")
        return {"rows": rows}


class GameDeals(A.ListScreen):
    key, short, feed, category, color, tag = "game_deals", "GAME DEALS", DealsFeed, "games", COLOR, "DEALS"

    def items(self, v):
        return [(r["t"], f"-{r['off']}% {r['rating']}".strip(), f"${r['sale']:.2f}") for r in (v or {}).get("rows", [])]


SCREENS = [FreeGames(), GameDeals()]
