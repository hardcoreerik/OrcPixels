"""Sports (milestone 2): ESPN's public scoreboard JSON (site.api.espn.com, keyless).

One screen per league (NFL, NBA, MLB, NHL, MLS, college football, college basketball,
WNBA, Premier League), plus "Live now" (every in-progress game) and "My teams"
(setting sports_favorites, e.g. "POR SEA NBA.LAL"). Scoreboards are fetched through a
shared 45 s cache so the aggregate screens never multiply requests; polling drops to
2 min while a league has a game in progress and is 15 min otherwise.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from PIL import ImageDraw

from .. import categories as CAT
from .. import live_adaptive as A
from .. import place as PLACE
from ..layout import Rect
from ..live_adaptive import AMB, CREAM, DIM, GREY, LPB, OG, RED, dimc, page_of
from ..sources import livefeeds as LF

BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = [  # key, path, tag, title
    ("nfl", "football/nfl", "NFL", "NFL"),
    ("nba", "basketball/nba", "NBA", "NBA"),
    ("mlb", "baseball/mlb", "MLB", "MLB"),
    ("nhl", "hockey/nhl", "NHL", "NHL"),
    ("mls", "soccer/usa.1", "MLS", "MLS"),
    ("ncaaf", "football/college-football", "NCAAF", "College football"),
    ("ncaab", "basketball/mens-college-basketball", "NCAAB", "College basketball"),
    ("wnba", "basketball/wnba", "WNBA", "WNBA"),
    ("epl", "soccer/eng.1", "EPL", "Premier League"),
]
COLOR = (255, 120, 40)
LIVE_C = (255, 70, 55)
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
TTL_S = 45.0

PLACE.CONFIG["sports_favorites"] = ("", "tag", "Favourite teams for My teams (ESPN abbreviations, e.g. POR SEA NBA.LAL)")
PLACE.PLACE_FEEDS.add("sports_fav")


def _hex(c, default=(200, 200, 200)):
    try:
        c = str(c or "").lstrip("#")
        rgb = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        m = max(rgb)
        if m < 170:                              # dark team colours vanish on an LED: lift
            rgb = tuple(min(255, int(v * 170 / max(m, 1)) + 40) for v in rgb) if m > 20 else (200, 200, 200)
        return rgb
    except (ValueError, IndexError):
        return default


def scoreboard(feed: LF.Feed, path: str) -> dict:
    url = f"{BASE}/{path}/scoreboard"
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and time.time() - hit[0] < TTL_S:
            return hit[1]
    data = feed.get_json(url)
    with _LOCK:
        _CACHE[url] = (time.time(), data)
    return data


def _detail(st: dict, start: float | None) -> str:
    t = st.get("type") or {}
    state = t.get("state")
    if state == "post":
        d = (t.get("shortDetail") or "FINAL").upper()
        return "FT" if d == "FT" else ("FINAL" if d.startswith("FINAL") and len(d) <= 5 else d.replace("FINAL/", "F/"))
    if state == "in":
        d = (t.get("shortDetail") or "LIVE").upper()
        if " - " in d:                           # "6:59 - 1ST" -> "1ST 6:59"
            a, b = d.split(" - ", 1)
            d = f"{b} {a}"
        return d.replace("HALFTIME", "HALF").replace("END OF ", "END ")
    if start:
        lt = PLACE.local(start)
        today = PLACE.local(time.time()).date()
        hm = lt.strftime("%I:%M").lstrip("0") + ("A" if lt.hour < 12 else "P")
        return hm if lt.date() == today else lt.strftime("%a").upper() + " " + hm
    return (t.get("shortDetail") or "").upper()


def parse(data: dict, tag: str) -> list[dict]:
    out = []
    for ev in data.get("events") or []:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
            a, h = teams.get("away"), teams.get("home")
            if not a or not h:
                continue
            start = None
            if ev.get("date"):
                start = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()
            st = ev.get("status") or comp.get("status") or {}
            state = (st.get("type") or {}).get("state", "pre")
            g = {"league": tag, "state": state, "start": start, "detail": _detail(st, start)}
            for side, c in (("a", a), ("h", h)):
                t = c.get("team") or {}
                g[side] = A.clean(t.get("abbreviation") or t.get("shortDisplayName") or "?")[:5]
                g[side + "s"] = c.get("score") if state != "pre" else ""
                g[side + "c"] = _hex(t.get("color"))
                g[side + "name"] = A.clean(t.get("shortDisplayName") or t.get("displayName") or "")
            out.append(g)
        except (TypeError, ValueError, KeyError):
            continue
    order = {"in": 0, "pre": 1, "post": 2}
    out.sort(key=lambda g: (order.get(g["state"], 3), g["start"] or 0))
    return out[:40]


def favorites() -> list[tuple[str | None, str]]:
    out = []
    for tok in str(PLACE.get("sports_favorites") or "").upper().split():
        lg, _, team = tok.rpartition(".")
        out.append((lg or None, team))
    return out


# ------------------------------------------------------------------ feeds
class LeagueFeed(LF.Feed):
    path = tag = ""
    poll_s = 900.0

    @property
    def url(self) -> str:  # type: ignore[override]
        return f"{BASE}/{self.path}/scoreboard"

    def fetch(self) -> dict:
        games = parse(scoreboard(self, self.path), self.tag)
        live = sum(g["state"] == "in" for g in games)
        self.poll_s = 120.0 if live else 900.0
        return {"league": self.tag, "games": games, "live": live}


class MultiFeed(LF.Feed):
    poll_s = 300.0

    def all_games(self) -> list[dict]:
        games, errs = [], 0
        for _k, path, tag, _t in LEAGUES:
            try:
                games += parse(scoreboard(self, path), tag)
            except Exception:  # noqa: BLE001  (one league down: the others still show)
                errs += 1
        if errs == len(LEAGUES):
            raise OSError("ESPN unreachable")
        return games


class LiveNowFeed(MultiFeed):
    key, title, source = "sports_live", "Sports: live now", "ESPN public scoreboards (all leagues)"
    url = f"{BASE}/<league>/scoreboard"
    poll_s = 120.0

    def fetch(self) -> dict:
        all_ = self.all_games()
        live = [g for g in all_ if g["state"] == "in"]
        nxt = sorted((g for g in all_ if g["state"] == "pre" and g["start"]), key=lambda g: g["start"])[:6]
        return {"games": live, "next": nxt}


class FavFeed(MultiFeed):
    key, title, source = "sports_fav", "Sports: my teams", "ESPN public scoreboards (set sports_favorites)"
    url = f"{BASE}/<league>/scoreboard"

    def fetch(self) -> dict:
        fav = favorites()
        if not fav:
            raise PLACE.NotConfigured("set sports_favorites (e.g. POR SEA)")
        games = [g for g in self.all_games()
                 if any((lg is None or lg == g["league"]) and t in (g["a"], g["h"]) for lg, t in fav)]
        return {"games": games, "teams": [t for _l, t in fav]}


# ------------------------------------------------------------------ screens
class GamesScreen(A.AdaptiveScreen):
    category = "sports"
    color = COLOR
    per = 8                                  # multiple of the 4-frame live pulse
    tag = ""

    def items(self, v):
        return (v or {}).get("games") or []

    def per_page(self, ctx):
        if ctx.h < 24:
            return 1
        sc = A.scale_for(ctx)
        body = ctx.h - (5 * sc + 5)
        cols = max(1, ctx.w // (64 * sc)) if ctx.aspect >= 1.5 else 1
        return max(1, body // (14 * sc)) * cols

    def strip_parts(self, v):
        out = [(self.tag or str(self.short), COLOR)]
        for g in self.items(v)[:16]:
            out += [(g["a"], g["ac"]), (str(g["as"]), CREAM), (g["h"], g["hc"]), (str(g["hs"]), CREAM),
                    (g["detail"], LIVE_C if g["state"] == "in" else GREY), ("*", DIM)]
        return out

    def empty(self, v):
        return "NO GAMES TODAY"

    def draw_ctx(self, ctx, img, v, k, now):
        games = self.items(v)
        title = str(self.short)
        if not games:
            A.nodata(img, ctx, title, COLOR, self.empty(v), GREY, self.empty_detail(v))
            return
        if A.is_strip(ctx):
            A.marquee(img, Rect(0, (ctx.h - 7) // 2, ctx.w, 7), self.strip_parts(v), k, self.loop_at(v, (ctx.w, ctx.h)))
            return
        pages = self.pages_at(v, ctx)
        p = page_of(k, pages, self.per)
        pp = self.per_page(ctx)
        page = games[p * pp:(p + 1) * pp]
        pulse = (k // 2) % 2 == 0
        if ctx.h < 24:
            g = page[0]
            if ctx.w < 40:                                   # tiny: AWY+score / HOM+score / status
                A.T(img, 0, 0, A.fit(f"{g['a']}{g['as']}", ctx.w), g["ac"])
                A.T(img, 0, 6, A.fit(f"{g['h']}{g['hs']}", ctx.w), g["hc"])
                if ctx.h >= 16:
                    A.T(img, 0, 11, A.fit(g["detail"], ctx.w), LIVE_C if g["state"] == "in" else GREY)
                return
            tag = g["league"] if self.tag in ("", "LIVE", "MY") else self.tag
            x = A.T(img, 1, 1, tag, COLOR)
            if g["state"] == "in":
                img.putpixel((x + 1, 3), LIVE_C if pulse else dimc(LIVE_C, 0.3))
            A.RT(img, ctx.w - 2, 1, A.fit(g["detail"], ctx.w - x - 6), LIVE_C if g["state"] == "in" else GREY)
            if ctx.h >= 14:
                self._line(img, 1, 9, ctx.w - 2, g)
            return
        sc = A.scale_for(ctx)
        y0 = A.header(img, ctx, title, COLOR, f"{p + 1}/{pages}" if pages > 1 else self.head_right(v))
        cols = max(1, ctx.w // (64 * sc)) if ctx.aspect >= 1.5 else 1
        cw = ctx.w // cols
        rows = max(1, pp // cols)
        for i, g in enumerate(page):
            c, r = divmod(i, rows)
            self._block(img, c * cw + 1, y0 + r * 14 * sc, cw - 2, g, sc, pulse)

    def head_right(self, v):
        return ""

    def empty_detail(self, v):
        return ""

    def _line(self, img, x, y, w, g):
        s1, s2 = str(g["as"]), str(g["hs"])
        xa = A.T(img, x, y, g["a"], g["ac"]) + 1
        if s1:
            xa = A.T(img, xa, y, s1, CREAM)
        A.T(img, xa + 1, y, "-", DIM)
        xr = x + w
        if s2:
            xr = A.RT(img, xr, y, s2, CREAM) - 2
        A.RT(img, xr, y, g["h"], g["hc"])

    def _block(self, img, x, y, w, g, sc, pulse):
        lead = None
        try:
            if g["state"] != "pre":
                lead = "a" if float(g["as"]) > float(g["hs"]) else ("h" if float(g["hs"]) > float(g["as"]) else None)
        except (TypeError, ValueError):
            pass
        d = ImageDraw.Draw(img)
        for j, side in enumerate(("a", "h")):
            yy = y + j * 6 * sc
            d.rectangle((x, yy, x + sc, yy + 5 * sc - 1), fill=g[side + "c"])
            A.T(img, x + 3 * sc, yy, g[side], CREAM if lead in (None, side) else GREY, sc)
            if g[side + "s"] != "":
                A.RT(img, x + w // 2 + 6 * sc, yy, str(g[side + "s"]), CREAM if lead in (None, side) else GREY, sc)
        st = g["detail"]
        col = LIVE_C if g["state"] == "in" else (GREY if g["state"] == "post" else LPB)
        xr = x + w - 1
        room = w - (w // 2 + 8 * sc)
        if g["state"] == "pre":                  # no scores: the start time gets the room
            room = w - 3 * sc - max(A.L.text_w(g["a"], sc), A.L.text_w(g["h"], sc)) - 4 * sc
        A.RT(img, xr, y, A.fit(st, room, sc), col, sc)
        if g["state"] == "in":
            d.rectangle((xr - sc + 1, y + 6 * sc + 1, xr, y + 7 * sc), fill=LIVE_C if pulse else dimc(LIVE_C, 0.3))
        if g.get("league") and self.tag in ("LIVE", "MY"):
            A.RT(img, xr, y + 6 * sc, g["league"], dimc(COLOR, 0.8), sc)


class LiveNowScreen(GamesScreen):
    key, short, feed, tag = "sports_live", "LIVE NOW", LiveNowFeed, "LIVE"

    def empty(self, v):
        n = (v or {}).get("next") or []
        return "NO LIVE GAMES" if not n else f"NEXT {n[0]['a']}-{n[0]['h']}"

    def empty_detail(self, v):
        n = (v or {}).get("next") or []
        return f"{n[0]['league']} {n[0]['detail']}" if n else ""


class FavScreen(GamesScreen):
    key, short, feed, tag = "sports_fav", "MY TEAMS", FavFeed, "MY"

    def head_right(self, v):
        return " ".join((v or {}).get("teams") or [])[:12]

    def empty(self, v):
        return "NO GAME TODAY"


SCREENS = [LiveNowScreen(), FavScreen()]
for _k, _path, _tag, _title in LEAGUES:
    _F = type(f"Espn_{_k}", (LeagueFeed,), {"key": f"sports_{_k}", "title": f"{_title} scores",
                                            "source": "ESPN public scoreboard", "path": _path, "tag": _tag})
    _S = type(f"Espn_{_k}_Screen", (GamesScreen,), {"key": f"sports_{_k}", "short": _tag, "feed": _F, "tag": _tag})
    SCREENS.append(_S())
for _s in SCREENS:
    CAT.assign(_s.key, "sports")
