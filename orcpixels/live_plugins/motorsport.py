"""Formula 1 from Jolpica-F1 (api.jolpi.ca/ergast: the community successor of the Ergast
API, keyless; asks for <= 4 requests/s and 500/hour). Next race countdown, driver
standings, last race podium. Polled every 6 h (1 h on race weekends), shared cache."""
from __future__ import annotations

import time

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (255, 50, 40)
BASE = "https://api.jolpi.ca/ergast/f1/current"
TEAM = {"mercedes": (0, 210, 190), "red_bull": (30, 65, 255), "ferrari": (230, 0, 0), "mclaren": (255, 128, 0),
        "aston_martin": (0, 110, 80), "alpine": (255, 135, 188), "williams": (0, 90, 255), "rb": (102, 146, 255),
        "sauber": (82, 226, 82), "audi": (190, 190, 190), "haas": (182, 186, 189), "cadillac": (200, 170, 90)}


def team_col(cid):
    return TEAM.get(cid or "", (200, 200, 200))


def _race_ts(r):
    try:
        return LF.parse_iso(f"{r['date']}T{r.get('time') or '12:00:00Z'}")
    except (KeyError, ValueError):
        return None


class F1Feed(LF.Feed):
    source = "Jolpica-F1 (Ergast successor, keyless)"

    @property
    def poll_s(self):  # type: ignore[override]
        nx = (self.value or {}).get("ts")
        return 3600.0 if nx and 0 < nx - time.time() < 4 * 86400 else 6 * 3600.0


class F1NextFeed(F1Feed):
    key, title, url = "f1_next", "F1: next race countdown", BASE + "/next.json"

    def fetch(self) -> dict:
        r = (T.cached(self, self.url, 1800.0)["MRData"]["RaceTable"].get("Races") or [None])[0]
        if not r:
            raise ValueError("season over")
        q = r.get("Qualifying") or {}
        return {"name": A.clean(r["raceName"]).upper().replace(" GRAND PRIX", " GP"), "round": r.get("round"),
                "city": A.clean(r["Circuit"]["Location"].get("locality")).upper(), "ts": _race_ts(r),
                "quali": _race_ts(q) if q.get("date") else None}


class F1DriversFeed(F1Feed):
    key, title, url = "f1_drivers", "F1: driver standings", BASE + "/driverstandings.json"

    def fetch(self) -> dict:
        sl = (T.cached(self, self.url, 1800.0)["MRData"]["StandingsTable"].get("StandingsLists") or [{}])[0]
        rows = [{"code": d["Driver"].get("code") or d["Driver"]["familyName"][:3].upper(),
                 "name": A.clean(d["Driver"]["familyName"]).upper(), "pts": d["points"], "wins": d.get("wins", "0"),
                 "team": (d.get("Constructors") or [{}])[0].get("constructorId")} for d in sl.get("DriverStandings") or []]
        if not rows:
            raise ValueError("no standings")
        return {"rows": rows, "round": sl.get("round")}


class F1LastFeed(F1Feed):
    key, title, url = "f1_last", "F1: last race result", BASE + "/last/results.json"

    def fetch(self) -> dict:
        r = (T.cached(self, self.url, 1800.0)["MRData"]["RaceTable"].get("Races") or [None])[0]
        if not r:
            raise ValueError("no result yet")
        res = [{"pos": x["position"], "code": x["Driver"].get("code") or x["Driver"]["familyName"][:3].upper(),
                "name": A.clean(x["Driver"]["familyName"]).upper(), "gap": (x.get("Time") or {}).get("time") or A.clean(x.get("status")).upper(),
                "team": x["Constructor"]["constructorId"]} for x in r.get("Results") or []]
        return {"name": A.clean(r["raceName"]).upper().replace(" GRAND PRIX", " GP"), "res": res}


class F1Next(T.ValueScreen):
    key, short, feed, category, color = "f1_next", "F1 NEXT", F1NextFeed, "sports", COLOR

    def value(self, v, now):
        ex = [("ROUND", str(v.get("round") or "?"), None), ("RACE", (LF.hm12(v["ts"]) + " " + LF.pt(v["ts"]).strftime("%a").upper()) if v.get("ts") else "TBD", None)]
        if v.get("quali"):
            ex.insert(1, ("QUALI", T.countdown(v["quali"], now), A.AMB))
        return {"big": T.countdown(v.get("ts"), now), "label": v["name"], "sub": v["city"], "col": A.CREAM,
                "right": f"R{v.get('round')}", "extras": ex}


class F1Drivers(A.ListScreen):
    key, short, feed, category, color, tag = "f1_drivers", "F1 DRIVERS", F1DriversFeed, "sports", COLOR, "F1"

    def head_right(self, v):
        return f"R{v.get('round')}"

    def items(self, v):
        return [(r["name"], f"{r['wins']} WINS", r["pts"]) for r in (v or {}).get("rows", [])[:10]]


class F1Last(A.ListScreen):
    key, short, feed, category, color, tag = "f1_last", "F1 RESULT", F1LastFeed, "sports", COLOR, "F1"

    def head_right(self, v):
        return (v or {}).get("name", "")[:10]

    def items(self, v):
        return [(r["name"], (v or {}).get("name", ""), r["gap"]) for r in (v or {}).get("res", [])[:10]]


SCREENS = [F1Next(), F1Drivers(), F1Last()]
for _s in SCREENS:
    CAT.assign(_s.key, "sports")
