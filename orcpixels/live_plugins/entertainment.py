"""Entertainment (milestone 2), all keyless:

  TVMaze (api.tvmaze.com): airing now, tonight's prime time, streaming today, premieres,
  top rated today, my shows (setting tv_favorites, "Severance/The Bear").
  iTunes RSS charts (itunes.apple.com/us/rss): top songs, albums, movies (iTunes box
  office stand-in: no free box-office API exists), podcasts.
  Wikipedia pageviews (wikimedia.org REST): yesterday's most-read articles (trending).

Schedules are fetched once per date through a shared 20 min cache (TVMaze asks for <= 20
calls / 10 s; we make a few an hour). "Now" is computed at draw time from the cached
schedule, so airing-now stays right between fetches.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import urllib.parse

from .. import categories as CAT
from .. import live_adaptive as A
from .. import place as PLACE
from ..sources import livefeeds as LF

COLOR = (255, 90, 170)
TV = "https://api.tvmaze.com"
UA = {"User-Agent": "OrcPixels (+https://github.com/hardcoreerik/OrcPixels)"}
_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()

PLACE.CONFIG["tv_country"] = ("US", "tag", "TV schedule country (ISO code, e.g. US, GB, CA)")
PLACE.CONFIG["tv_favorites"] = ("", "id", "My shows (TVMaze names, separated by /)")
PLACE.PLACE_FEEDS.update({"tv_now", "tv_tonight", "tv_streaming", "tv_premieres", "tv_top", "tv_favorites"})


def cached(feed: LF.Feed, url: str, ttl: float, headers=None):
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    data = feed.get_json(url, headers or UA)
    with _LOCK:
        _CACHE[url] = (time.time(), data)
    return data


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hm(ts: float) -> str:
    lt = PLACE.local(ts)
    return lt.strftime("%I:%M").lstrip("0") + ("A" if lt.hour < 12 else "P")


def _ep(e: dict) -> dict | None:
    show = e.get("show") or (e.get("_embedded") or {}).get("show") or {}
    name = A.clean(show.get("name"))
    start = _ts(e.get("airstamp"))
    if not name or start is None:
        return None
    net = show.get("network") or show.get("webChannel") or {}
    return {"show": name, "net": A.clean(net.get("name")) or "TV", "start": start,
            "end": start + 60 * float(e.get("runtime") or show.get("runtime") or 30),
            "season": e.get("season"), "number": e.get("number"), "type": e.get("type"),
            "rating": (show.get("rating") or {}).get("average"), "web": "webChannel" in show and not show.get("network"),
            "premiered": show.get("premiered")}


def schedules(feed: LF.Feed) -> list[dict]:
    """Today's network + streaming schedule (local date), parsed and de-duplicated."""
    day = PLACE.local(time.time()).date().isoformat()
    cc = str(PLACE.get("tv_country") or "US").upper()
    out, seen, ok = [], set(), 0
    for url in (f"{TV}/schedule?country={cc}&date={day}", f"{TV}/schedule/web?date={day}&country={cc}",
                f"{TV}/schedule/web?date={day}&country="):             # country= : global platforms (Netflix, YouTube)
        try:
            data = cached(feed, url, 1200.0)
            ok += 1
        except Exception:  # noqa: BLE001
            continue
        for e in data or []:
            ep = _ep(e)
            if ep and (ep["show"], ep["start"]) not in seen:
                seen.add((ep["show"], ep["start"]))
                out.append(ep)
    if not ok:
        raise OSError("TVMaze unreachable")
    return sorted(out, key=lambda e: e["start"])


def se(e) -> str:
    s, n = e.get("season"), e.get("number")
    if s and n and int(s) < 1000:
        return f"S{s}E{n}"
    return ""


# ------------------------------------------------------------------ feeds
class TvFeed(LF.Feed):
    source, poll_s = "TVMaze schedule API (keyless)", 900.0
    url = f"{TV}/schedule"

    def fetch(self) -> dict:
        return {"eps": schedules(self), "day": PLACE.local(time.time()).date().isoformat()}


class TvNowFeed(TvFeed):
    key, title, poll_s = "tv_now", "TV: airing now", 300.0

    def fetch(self) -> dict:              # "now" is taken at fetch time (every 5 min, cached schedule)
        v = super().fetch()
        t = time.time()
        on = [e for e in v["eps"] if e["start"] <= t < e["end"] and not e["web"]]
        v["on"] = sorted(on, key=lambda e: -(e["rating"] or 0))[:12]
        return v


class TvTonightFeed(TvFeed):
    key, title = "tv_tonight", "TV: tonight (prime time)"


class TvStreamFeed(TvFeed):
    key, title = "tv_streaming", "TV: streaming today"


class TvPremFeed(TvFeed):
    key, title = "tv_premieres", "TV: premieres today"


class TvTopFeed(TvFeed):
    key, title = "tv_top", "TV: top rated today"


class TvFavFeed(LF.Feed):
    key, title, source, poll_s = "tv_favorites", "TV: my shows", "TVMaze (set tv_favorites)", 6 * 3600.0
    url = f"{TV}/singlesearch/shows?q=<show>&embed=nextepisode"

    def fetch(self) -> dict:
        names = [n.strip() for n in str(PLACE.get("tv_favorites") or "").split("/") if n.strip()][:8]
        if not names:
            raise PLACE.NotConfigured("set tv_favorites (e.g. Severance/The Bear)")
        out = []
        for n in names:
            try:
                s = cached(self, f"{TV}/singlesearch/shows?q={urllib.parse.quote(n)}&embed=nextepisode", 6 * 3600.0)
            except Exception:  # noqa: BLE001
                out.append({"show": A.clean(n), "next": None, "status": "NOT FOUND"})
                continue
            nx = (s.get("_embedded") or {}).get("nextepisode") or {}
            out.append({"show": A.clean(s.get("name") or n), "next": _ts(nx.get("airstamp")),
                        "se": se(nx), "status": A.clean(s.get("status") or "").upper(),
                        "net": A.clean(((s.get("network") or s.get("webChannel")) or {}).get("name"))})
        if all(r.get("status") == "NOT FOUND" for r in out):
            raise OSError("TVMaze unreachable or no show found")
        out.sort(key=lambda r: (r["next"] is None, r["next"] or 0))
        return {"shows": out}


class ItunesFeed(LF.Feed):
    chart = ""
    source, poll_s = "iTunes RSS charts (keyless)", 6 * 3600.0

    @property
    def url(self):  # type: ignore[override]
        return f"https://itunes.apple.com/us/rss/{self.chart}/limit=10/json"

    def fetch(self) -> dict:
        data = cached(self, self.url, 3600.0)
        rows = []
        for e in ((data.get("feed") or {}).get("entry") or [])[:10]:
            name = A.clean((e.get("im:name") or {}).get("label"))
            who = A.clean((e.get("im:artist") or {}).get("label"))
            cat = A.clean(((e.get("category") or {}).get("attributes") or {}).get("label"))
            if name:
                rows.append({"name": name, "by": who, "cat": cat})
        if not rows:
            raise ValueError("empty chart")
        return {"rows": rows}


class SongsFeed(ItunesFeed):
    key, title, chart = "top_songs", "Top songs (iTunes)", "topsongs"


class AlbumsFeed(ItunesFeed):
    key, title, chart = "top_albums", "Top albums (iTunes)", "topalbums"


class MoviesFeed(ItunesFeed):
    key, title, chart = "top_movies", "Top movies (iTunes)", "topmovies"


class PodcastsFeed(ItunesFeed):
    key, title, chart = "top_podcasts", "Top podcasts (Apple)", "toppodcasts"


SKIP = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "File:", "Help:", "Category:", "Template:", "Talk:", "User:", "-")


class WikiFeed(LF.Feed):
    key, title, source, poll_s = "wiki_trending", "Trending on Wikipedia", "Wikimedia pageviews API (keyless)", 6 * 3600.0
    url = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia.org/all-access/<yesterday>"

    def fetch(self) -> dict:
        last = None
        for back in (1, 2):
            d = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=back)
            try:
                data = cached(self, f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia.org/all-access/{d:%Y/%m/%d}", 3 * 3600.0)
                break
            except Exception as e:  # noqa: BLE001  (yesterday not computed yet: day before)
                last = e
        else:
            raise last or OSError("no data")
        arts = ((data.get("items") or [{}])[0].get("articles") or [])
        rows = [{"name": A.clean(a["article"].replace("_", " ")), "views": a.get("views")}
                for a in arts if not a["article"].startswith(SKIP)]
        return {"rows": [r for r in rows if r["name"]][:10], "date": d.isoformat()}


# ------------------------------------------------------------------ screens
class EntList(A.ListScreen):
    category, color, per = "entertainment", COLOR, 8


class TvScreen(EntList):
    def eps(self, v, now):
        return v.get("eps") or []

    def items(self, v):
        return [(e["show"], e["net"], self.right(e)) for e in self.eps(v or {}, None)]

    def right(self, e):
        return hm(e["start"])


class TvNow(TvScreen):
    key, short, feed, tag = "tv_now", "ON NOW", TvNowFeed, "NOW"

    def eps(self, v, now):
        return v.get("on") or []

    def right(self, e):
        return "TIL " + hm(e["end"])

    def empty_msg(self, v):
        return "NOTHING ON AIR"


class TvTonight(TvScreen):
    key, short, feed, tag = "tv_tonight", "TONIGHT", TvTonightFeed, "8-11P"

    def eps(self, v, now):
        out = []
        for e in v.get("eps") or []:
            h = PLACE.local(e["start"]).hour
            if 20 <= h < 23 and not e["web"]:
                out.append(e)
        return out[:16]

    def empty_msg(self, v):
        return "NO PRIME TIME LISTINGS"


class TvStream(TvScreen):
    key, short, feed, tag = "tv_streaming", "STREAMING", TvStreamFeed, "WEB"

    def eps(self, v, now):
        seen, out = set(), []
        for e in v.get("eps") or []:
            if e["web"] and e["show"] not in seen:
                seen.add(e["show"])
                out.append(e)
        return sorted(out, key=lambda e: -(e["rating"] or 0))[:12]

    def right(self, e):
        return se(e)


class TvPrem(TvScreen):
    key, short, feed, tag = "tv_premieres", "PREMIERES", TvPremFeed, "NEW"

    def eps(self, v, now):
        day = v.get("day")
        return [e for e in v.get("eps") or [] if e.get("number") == 1 or e.get("premiered") == day][:12]

    def right(self, e):
        return f"S{e['season']} PREM" if e.get("season") and int(e["season"]) < 1000 else "NEW"

    def empty_msg(self, v):
        return "NO PREMIERES TODAY"


class TvTop(TvScreen):
    key, short, feed, tag = "tv_top", "TOP RATED", TvTopFeed, "TOP"

    def eps(self, v, now):
        seen, out = set(), []
        for e in sorted((e for e in v.get("eps") or [] if e["rating"]), key=lambda e: -e["rating"]):
            if e["show"] not in seen:
                seen.add(e["show"])
                out.append(e)
        return out[:10]

    def right(self, e):
        return f"{e['rating']:.1f}"


class TvFav(EntList):
    key, short, feed, tag = "tv_favorites", "MY SHOWS", TvFavFeed, "MINE"

    def items(self, v):
        out = []
        for r in (v or {}).get("shows") or []:
            if r["next"]:
                lt = PLACE.local(r["next"])
                when = lt.strftime("%a").upper() + " " + hm(r["next"])
                out.append((r["show"], r.get("se") or r.get("net") or "", when))
            else:
                out.append((r["show"], r.get("net") or "", r.get("status") or "TBA"))
        return out


class Chart(EntList):
    def items(self, v):
        return [(r["name"], r.get("by") or "", "") for r in (v or {}).get("rows") or []]


class TopSongs(Chart):
    key, short, feed, tag = "top_songs", "TOP SONGS", SongsFeed, "SONGS"


class TopAlbums(Chart):
    key, short, feed, tag = "top_albums", "TOP ALBUMS", AlbumsFeed, "ALBUMS"


class TopMovies(Chart):
    key, short, feed, tag = "top_movies", "TOP MOVIES", MoviesFeed, "MOVIES"

    def items(self, v):
        return [(r["name"], r.get("cat") or r.get("by") or "", "") for r in (v or {}).get("rows") or []]


class TopPodcasts(Chart):
    key, short, feed, tag = "top_podcasts", "PODCASTS", PodcastsFeed, "PODS"


class WikiTrend(EntList):
    key, short, feed, tag = "wiki_trending", "TRENDING", WikiFeed, "WIKI"

    def items(self, v):
        return [(r["name"], "WIKIPEDIA", A.short_num(r.get("views"))) for r in (v or {}).get("rows") or []]


SCREENS = [TvNow(), TvTonight(), TvStream(), TvPrem(), TvTop(), TvFav(), TopSongs(), TopAlbums(), TopMovies(),
           TopPodcasts(), WikiTrend()]
for _s in SCREENS:
    CAT.assign(_s.key, "entertainment")
