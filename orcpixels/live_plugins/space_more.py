"""More space sources (2026-10-14), all keyless except NASA NeoWs, which uses NASA's public
DEMO_KEY until you paste your own free key (Settings > Free sources):

  Launch Library 2 (thespacedevs.com, free tier 15 requests/hour): next launch countdown + upcoming list
  Spaceflight News API v4 (spaceflightnewsapi.net): latest headlines
  People in space (corquaid ISS APIs on GitHub Pages, a static JSON kept up to date by hand)
  NASA NeoWs (api.nasa.gov): asteroids passing Earth this week (DEMO_KEY: 30/hour, 50/day per IP)
  NASA EONET v3 (eonet.gsfc.nasa.gov): open natural events (storms, wildfires, volcanoes), nearest first
"""
from __future__ import annotations

import datetime as dt
import time

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (180, 120, 255)
GO, HOLD = (60, 225, 90), (255, 168, 20)
LL2 = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=10&mode=list"


def _ts(s):
    try:
        return LF.parse_iso(str(s))
    except (TypeError, ValueError):
        return None


def _when(ts):
    if ts is None:
        return "TBD"
    lt = LF.pt(ts)
    return lt.strftime("%b %d").upper().replace(" 0", " ")


# ------------------------------------------------------------------ launches
class LaunchFeed(LF.Feed):
    source, poll_s = "Launch Library 2 (thespacedevs, keyless, 15/h)", 1800.0
    url = LL2

    def fetch(self) -> dict:
        d = T.cached(self, LL2, 1500.0)
        rows = []
        for r in d.get("results") or []:
            name = A.clean(r.get("name"))
            rocket, _, mission = name.partition(" | ")
            st = (r.get("status") or {}).get("abbrev") or ""
            if st in ("Success", "Failure", "Partial Failure"):
                continue
            rows.append({"rocket": rocket.upper(), "mission": (mission or rocket).upper(), "net": _ts(r.get("net")),
                         "status": st.upper(), "prec": ((r.get("net_precision") or {}).get("abbrev") or "").upper()})
        rows.sort(key=lambda r: r["net"] or 9e12)
        if not rows:
            raise ValueError("no upcoming launches")
        return {"rows": rows}


class NextLaunchFeed(LaunchFeed):
    key, title = "launch_next", "Next rocket launch (countdown)"


class LaunchesFeed(LaunchFeed):
    key, title = "launches", "Upcoming launches"


class NextLaunch(T.ValueScreen):
    key, short, feed, category, color = "launch_next", "NEXT LAUNCH", NextLaunchFeed, "space", COLOR

    def value(self, v, now):
        rows = [r for r in v["rows"] if (r["net"] or 9e12) > now - 3600] or v["rows"]
        r = rows[0]
        st = r["status"]
        col = GO if st == "GO" else HOLD
        cd = T.countdown(r["net"], now)
        return {"big": "T-" + cd if cd not in ("NOW", "DONE", "TBD") else cd, "label": "NEXT LAUNCH", "sub": r["mission"],
                "col": col, "right": st,
                "extras": [("ROCKET", r["rocket"], None), ("STATUS", st or "?", col),
                           ("NET", (LF.hm12(r["net"]) + " " + _when(r["net"])) if r["net"] else "TBD", None)]}


class Launches(A.ListScreen):
    key, short, feed, category, color, tag = "launches", "LAUNCHES", LaunchesFeed, "space", COLOR, "LAUNCH"

    def items(self, v):
        return [(r["mission"], r["rocket"], _when(r["net"])) for r in (v or {}).get("rows") or []][:10]


# ------------------------------------------------------------------ news
class NewsFeed(LF.Feed):
    key, title, source, poll_s = "space_news", "Spaceflight news", "Spaceflight News API v4 (keyless)", 1800.0
    url = "https://api.spaceflightnewsapi.net/v4/articles/?limit=10"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 1500.0)
        rows = [{"title": A.clean(r.get("title")), "site": A.clean(r.get("news_site")).upper(), "at": _ts(r.get("published_at"))}
                for r in d.get("results") or [] if r.get("title")]
        if not rows:
            raise ValueError("no articles")
        return {"rows": rows}


class SpaceNews(A.ListScreen):
    key, short, feed, category, color, tag = "space_news", "SPACE NEWS", NewsFeed, "space", COLOR, "SPACE"

    def items(self, v):
        now = time.time()
        return [(r["title"], r["site"], LF.ago(r["at"], now)) for r in (v or {}).get("rows") or []]


# ------------------------------------------------------------------ people in space
class AstrosFeed(LF.Feed):
    key, title, source, poll_s = "astros", "People in space", "corquaid people-in-space JSON (keyless)", 6 * 3600.0
    url = "https://corquaid.github.io/international-space-station-APIs/JSON/people-in-space.json"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 3 * 3600.0)
        ppl = [{"name": A.clean(p.get("name")).upper(), "craft": A.clean(p.get("spacecraft")).upper(),
                "iss": bool(p.get("iss")), "days": p.get("days_in_space")} for p in d.get("people") or []]
        if not ppl:
            raise ValueError("no crew list")
        return {"n": int(d.get("number") or len(ppl)), "people": ppl, "exp": d.get("iss_expedition")}


class Astros(T.ValueScreen):
    key, short, feed, category, color = "astros", "IN SPACE", AstrosFeed, "space", COLOR

    def value(self, v, now):
        iss = sum(1 for p in v["people"] if p["iss"])
        return {"big": str(v["n"]), "label": "IN SPACE", "sub": f"ISS {iss} / OTHER {v['n'] - iss}", "col": A.CREAM,
                "right": f"EXP {v['exp']}" if v.get("exp") else "",
                "extras": [(p["name"].split()[-1], "ISS" if p["iss"] else p["craft"], None) for p in v["people"]]}


# ------------------------------------------------------------------ NASA NeoWs (free key, DEMO_KEY fallback)
class NeoFeed(T.KeyedFeed):
    key, title, source, poll_s = "neo", "Asteroids passing Earth (NASA)", "NASA NeoWs (DEMO_KEY or your free key)", 6 * 3600.0
    service = "nasa"
    url = "https://api.nasa.gov/neo/rest/v1/feed?start_date=<today>&api_key=<key>"

    def fetch(self) -> dict:
        day = dt.datetime.fromtimestamp(time.time(), dt.timezone.utc).date().isoformat()
        d = T.cached(self, f"https://api.nasa.gov/neo/rest/v1/feed?start_date={day}&api_key={T.key_for('nasa')}", 5 * 3600.0)
        rows = []
        for objs in (d.get("near_earth_objects") or {}).values():
            for o in objs:
                ca = (o.get("close_approach_data") or [{}])[0]
                dia = ((o.get("estimated_diameter") or {}).get("meters") or {})
                rows.append({"name": A.clean(o.get("name")).strip("()").upper(), "haz": bool(o.get("is_potentially_hazardous_asteroid")),
                             "ld": float((ca.get("miss_distance") or {}).get("lunar") or 0),
                             "m": round(((dia.get("estimated_diameter_min") or 0) + (dia.get("estimated_diameter_max") or 0)) / 2),
                             "at": (ca.get("epoch_date_close_approach") or 0) / 1000.0,
                             "kps": float((ca.get("relative_velocity") or {}).get("kilometers_per_second") or 0)})
        if not rows:
            raise ValueError("no objects")
        rows.sort(key=lambda r: r["ld"])
        return {"rows": rows[:12], "count": int(d.get("element_count") or len(rows))}


class Neo(A.ListScreen):
    key, short, feed, category, color, tag = "neo", "ASTEROIDS", NeoFeed, "space", COLOR, "NEO"

    def head_right(self, v):
        return f"{v.get('count', 0)} WK"

    def items(self, v):
        return [(("! " if r["haz"] else "") + r["name"], f"{r['m']}M {r['kps']:.0f}KM/S", f"{r['ld']:.1f}LD")
                for r in (v or {}).get("rows") or []]


# ------------------------------------------------------------------ NASA EONET natural events
EV = {"wildfires": ("FIRE", (255, 120, 40)), "severeStorms": ("STORM", (120, 200, 255)), "volcanoes": ("VOLC", (255, 50, 40)),
      "seaLakeIce": ("ICE", (200, 230, 255)), "floods": ("FLOOD", (60, 140, 255)), "earthquakes": ("QUAKE", (255, 168, 20)),
      "landslides": ("SLIDE", (180, 140, 90)), "dustHaze": ("DUST", (200, 170, 110)), "snow": ("SNOW", (235, 240, 255)),
      "drought": ("DRY", (220, 180, 60)), "tempExtremes": ("TEMP", (255, 90, 170)), "waterColor": ("WATER", (60, 200, 200)),
      "manmade": ("MANMADE", (150, 156, 170))}


class EonetFeed(LF.Feed):
    key, title, source, poll_s = "eonet", "Natural events now (NASA EONET)", "NASA EONET v3 (keyless)", 3600.0
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30&limit=40"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 3000.0)
        try:
            home = T.home_latlon()
        except Exception:  # noqa: BLE001  (works without a location: newest first)
            home = None
        rows = []
        for e in d.get("events") or []:
            cat = ((e.get("categories") or [{}])[0]).get("id", "")
            g = (e.get("geometry") or [{}])[-1]
            c = g.get("coordinates") or []
            while c and isinstance(c[0], list):           # polygons: first vertex is close enough
                c = c[0]
            dist = T.km(home, (c[1], c[0])) if home and len(c) >= 2 else None
            title = A.clean(e.get("title")).upper()
            rows.append({"title": title, "cat": cat, "km": dist, "at": _ts(g.get("date")),
                         "mag": f"{g['magnitudeValue']:.0f}{(g.get('magnitudeUnit') or '').upper()}" if g.get("magnitudeValue") else ""})
        if not rows:
            raise ValueError("no open events")
        rows.sort(key=lambda r: (r["km"] is None, r["km"] if r["km"] is not None else -(r["at"] or 0)))
        return {"rows": rows[:12], "n": len(d.get("events") or [])}


class Eonet(A.ListScreen):
    key, short, feed, category, color, tag = "eonet", "EARTH EVENTS", EonetFeed, "space", COLOR, "EONET"

    def head_right(self, v):
        return f"{v.get('n', 0)} OPEN"

    def items(self, v):
        out = []
        for r in (v or {}).get("rows") or []:
            tag = EV.get(r["cat"], ("EVENT", None))[0]
            dist = f"{r['km']:,.0f}KM" if r["km"] is not None else r["mag"]
            out.append((r["title"], tag + (" " + r["mag"] if r["mag"] and r["km"] is not None else ""), dist))
        return out


SCREENS = [NextLaunch(), Launches(), SpaceNews(), Astros(), Neo(), Eonet()]
T.KEYS["nasa"].setdefault("used_by", []).append("neo")
for _s in SCREENS:
    CAT.assign(_s.key, "space")
